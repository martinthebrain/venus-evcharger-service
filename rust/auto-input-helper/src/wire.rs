//! Decoder for gateway-owned semantic energy snapshots.

use std::collections::BTreeSet;
use std::fs::File;
use std::io::Read;
use std::path::Path;

use crate::error::{HelperError, Result};

const MAGIC_V3: &[u8; 4] = b"VEI3";
const MAGIC_V4: &[u8; 4] = b"VEI4";
const SCHEMA_VERSION_V3: u8 = 3;
const SCHEMA_VERSION_V4: u8 = 4;
const MAX_PAYLOAD_BYTES: u64 = 65_536;
const MAX_SOURCE_IDS: usize = 64;

/// Semantic quality state published by the `DBus` gateway.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MeasurementStatus {
    Fresh,
    Stale,
    Unavailable,
    Error,
    Unknown,
}

impl MeasurementStatus {
    /// Return whether this status may contribute to Auto decisions.
    #[must_use]
    pub const fn contributes(self) -> bool {
        matches!(self, Self::Fresh | Self::Stale)
    }
}

/// One transport-neutral measurement from the gateway.
#[derive(Clone, Debug, PartialEq)]
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
    /// Return whether the value is usable at `now_monotonic`.
    #[must_use]
    pub fn usable(&self, now_monotonic: f64, maximum_age: f64) -> bool {
        if !self.status.contributes() || self.value.is_none() {
            return false;
        }
        let observed = self.observed_monotonic;
        observed.is_finite()
            && observed > 0.0
            && observed <= now_monotonic
            && now_monotonic - observed <= maximum_age
    }
}

/// One coherent set of gateway inputs.
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

/// Load and decode one fresh energy-input file.
///
/// # Errors
///
/// Returns an error for absent, oversized, malformed, or stale input.
pub fn load_energy_inputs(
    path: &Path,
    now_monotonic: f64,
    maximum_age: f64,
) -> Result<EnergyInputs> {
    let bytes = read_bounded(path)?;
    let snapshot = decode_energy_inputs(&bytes)?;
    let age = now_monotonic - snapshot.captured_monotonic;
    if !now_monotonic.is_finite()
        || now_monotonic < 0.0
        || !maximum_age.is_finite()
        || age < 0.0
        || (maximum_age >= 0.0 && age > maximum_age)
    {
        return Err(HelperError::Input(
            "semantic energy snapshot is stale or has an invalid clock".to_owned(),
        ));
    }
    Ok(snapshot)
}

/// Decode a complete `VEI4` payload or its `VEI3` predecessor.
///
/// # Errors
///
/// Returns an error for any wire-contract violation.
pub fn decode_energy_inputs(payload: &[u8]) -> Result<EnergyInputs> {
    if payload.len() > usize::try_from(MAX_PAYLOAD_BYTES).unwrap_or(usize::MAX) {
        return Err(HelperError::Input(
            "energy inputs payload exceeds 65536 bytes".to_owned(),
        ));
    }
    let mut reader = Reader::new(payload);
    let magic = reader.bytes(4)?;
    let schema_version = reader.u8()?;
    let measurement_count = match (magic, schema_version) {
        (value, SCHEMA_VERSION_V3) if value == MAGIC_V3 => 4,
        (value, SCHEMA_VERSION_V4) if value == MAGIC_V4 => 7,
        (value, _) if value == MAGIC_V3 || value == MAGIC_V4 => {
            return Err(HelperError::Input(
                "energy inputs payload has unsupported schema".to_owned(),
            ));
        }
        _ => {
            return Err(HelperError::Input(
                "energy inputs payload has invalid magic".to_owned(),
            ));
        }
    };
    let sequence = reader.u64()?;
    let topology_generation = reader.u64()?;
    let captured_at = reader.f64()?;
    let captured_monotonic = reader.f64()?;
    require_non_negative_finite(captured_at, "captured_at")?;
    require_non_negative_finite(captured_monotonic, "captured_monotonic")?;
    let grid_power_w = reader.measurement(captured_monotonic)?;
    let pv_power_w = reader.measurement(captured_monotonic)?;
    let battery_soc = reader.measurement(captured_monotonic)?;
    let battery_net_power_w = reader.measurement(captured_monotonic)?;
    let watt_hour_capacity = if measurement_count == 7 {
        reader.measurement(captured_monotonic)?
    } else {
        unavailable_measurement()
    };
    let amp_hour_capacity = if measurement_count == 7 {
        reader.measurement(captured_monotonic)?
    } else {
        unavailable_measurement()
    };
    let battery_voltage = if measurement_count == 7 {
        reader.measurement(captured_monotonic)?
    } else {
        unavailable_measurement()
    };
    if !reader.complete() {
        return Err(HelperError::Input(
            "energy inputs payload has trailing data".to_owned(),
        ));
    }
    Ok(EnergyInputs {
        sequence,
        topology_generation,
        captured_at,
        captured_monotonic,
        grid_power_w,
        pv_power_w,
        battery_soc,
        battery_net_power_w,
        battery_capacity_wh: watt_hour_capacity,
        battery_capacity_ah: amp_hour_capacity,
        battery_voltage_v: battery_voltage,
    })
}

fn unavailable_measurement() -> Measurement {
    Measurement {
        value: None,
        observed_at: 0.0,
        observed_monotonic: 0.0,
        status: MeasurementStatus::Unknown,
        confidence: 0.0,
        source_ids: Vec::new(),
        reason_code: "not-observed".to_owned(),
    }
}

fn read_bounded(path: &Path) -> Result<Vec<u8>> {
    let file = File::open(path).map_err(|error| HelperError::input("energy inputs", &error))?;
    let size = file
        .metadata()
        .map_err(|error| HelperError::input("energy inputs", &error))?
        .len();
    if size > MAX_PAYLOAD_BYTES {
        return Err(HelperError::Input(
            "energy inputs payload exceeds 65536 bytes".to_owned(),
        ));
    }
    let mut bytes = Vec::with_capacity(usize::try_from(size).unwrap_or(0));
    file.take(MAX_PAYLOAD_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| HelperError::input("energy inputs", &error))?;
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_PAYLOAD_BYTES {
        return Err(HelperError::Input(
            "energy inputs payload exceeds 65536 bytes".to_owned(),
        ));
    }
    Ok(bytes)
}

fn require_non_negative_finite(value: f64, label: &str) -> Result<()> {
    if value.is_finite() && value >= 0.0 {
        return Ok(());
    }
    Err(HelperError::Input(format!(
        "energy inputs {label} is invalid"
    )))
}

struct Reader<'a> {
    payload: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    const fn new(payload: &'a [u8]) -> Self {
        Self { payload, offset: 0 }
    }

    fn bytes(&mut self, count: usize) -> Result<&'a [u8]> {
        let end = self
            .offset
            .checked_add(count)
            .ok_or_else(|| HelperError::Input("energy inputs offset overflow".to_owned()))?;
        let bytes = self
            .payload
            .get(self.offset..end)
            .ok_or_else(|| HelperError::Input("energy inputs payload is truncated".to_owned()))?;
        self.offset = end;
        Ok(bytes)
    }

    fn u8(&mut self) -> Result<u8> {
        self.bytes(1).map(|bytes| bytes[0])
    }

    fn u16(&mut self) -> Result<u16> {
        let bytes: [u8; 2] = self
            .bytes(2)?
            .try_into()
            .map_err(|_error| HelperError::Input("invalid u16 field".to_owned()))?;
        Ok(u16::from_be_bytes(bytes))
    }

    fn u64(&mut self) -> Result<u64> {
        let bytes: [u8; 8] = self
            .bytes(8)?
            .try_into()
            .map_err(|_error| HelperError::Input("invalid u64 field".to_owned()))?;
        Ok(u64::from_be_bytes(bytes))
    }

    fn f64(&mut self) -> Result<f64> {
        self.u64().map(f64::from_bits)
    }

    fn text(&mut self) -> Result<String> {
        let length = usize::from(self.u16()?);
        let bytes = self.bytes(length)?;
        String::from_utf8(bytes.to_vec()).map_err(|error| {
            HelperError::Input(format!("energy inputs text is not UTF-8: {error}"))
        })
    }

    fn measurement(&mut self, captured_monotonic: f64) -> Result<Measurement> {
        let status = match self.u8()? {
            0 => MeasurementStatus::Fresh,
            1 => MeasurementStatus::Stale,
            2 => MeasurementStatus::Unavailable,
            3 => MeasurementStatus::Error,
            4 => MeasurementStatus::Unknown,
            _ => {
                return Err(HelperError::Input(
                    "energy inputs measurement has invalid status".to_owned(),
                ));
            }
        };
        let has_value = self.u8()?;
        let raw_value = self.f64()?;
        let value = match has_value {
            0 => None,
            1 if raw_value.is_finite() => Some(raw_value),
            1 => {
                return Err(HelperError::Input(
                    "energy inputs measurement value is not finite".to_owned(),
                ));
            }
            _ => {
                return Err(HelperError::Input(
                    "energy inputs measurement has invalid value marker".to_owned(),
                ));
            }
        };
        let observed_at = self.f64()?;
        let observed_monotonic = self.f64()?;
        let confidence = self.f64()?;
        require_non_negative_finite(observed_at, "measurement observed_at")?;
        require_non_negative_finite(observed_monotonic, "measurement observed_monotonic")?;
        if observed_monotonic > captured_monotonic {
            return Err(HelperError::Input(
                "measurement timestamp exceeds snapshot clock".to_owned(),
            ));
        }
        if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
            return Err(HelperError::Input(
                "measurement confidence is outside zero to one".to_owned(),
            ));
        }
        if status.contributes()
            && (value.is_none() || observed_at <= 0.0 || observed_monotonic <= 0.0)
        {
            return Err(HelperError::Input(
                "contributing measurement lacks a value or observation clock".to_owned(),
            ));
        }
        let source_count = usize::from(self.u16()?);
        if source_count > MAX_SOURCE_IDS {
            return Err(HelperError::Input(
                "measurement has too many source identifiers".to_owned(),
            ));
        }
        let mut source_ids = Vec::with_capacity(source_count);
        let mut unique = BTreeSet::new();
        for _index in 0..source_count {
            let source_id = self.text()?;
            if source_id.trim().is_empty() || !unique.insert(source_id.clone()) {
                return Err(HelperError::Input(
                    "measurement source identifiers are empty or duplicated".to_owned(),
                ));
            }
            source_ids.push(source_id);
        }
        let reason_code = self.text()?;
        Ok(Measurement {
            value,
            observed_at,
            observed_monotonic,
            status,
            confidence,
            source_ids,
            reason_code,
        })
    }

    const fn complete(&self) -> bool {
        self.offset == self.payload.len()
    }
}

#[cfg(test)]
mod tests {
    use super::{MeasurementStatus, decode_energy_inputs};

    fn text(value: &str, target: &mut Vec<u8>) {
        let Ok(length) = u16::try_from(value.len()) else {
            return;
        };
        target.extend(length.to_be_bytes());
        target.extend(value.as_bytes());
    }

    fn measurement(value: Option<f64>, status: u8, observed: f64, target: &mut Vec<u8>) {
        target.push(status);
        target.push(u8::from(value.is_some()));
        target.extend(value.unwrap_or(0.0).to_bits().to_be_bytes());
        target.extend(1_700_000_000.0_f64.to_bits().to_be_bytes());
        target.extend(observed.to_bits().to_be_bytes());
        target.extend(0.9_f64.to_bits().to_be_bytes());
        target.extend(1_u16.to_be_bytes());
        text("source", target);
        text("", target);
    }

    fn payload(version: u8) -> Vec<u8> {
        let mut payload = if version == 3 {
            b"VEI3".to_vec()
        } else {
            b"VEI4".to_vec()
        };
        payload.push(version);
        payload.extend(7_u64.to_be_bytes());
        payload.extend(2_u64.to_be_bytes());
        payload.extend(1_700_000_001.0_f64.to_bits().to_be_bytes());
        payload.extend(100.0_f64.to_bits().to_be_bytes());
        measurement(Some(-250.0), 0, 99.0, &mut payload);
        measurement(Some(2_000.0), 0, 99.0, &mut payload);
        measurement(Some(55.0), 0, 99.0, &mut payload);
        measurement(Some(-500.0), 1, 98.0, &mut payload);
        if version == 4 {
            measurement(Some(5_120.0), 0, 99.0, &mut payload);
            measurement(Some(100.0), 0, 99.0, &mut payload);
            measurement(Some(52.8), 0, 99.0, &mut payload);
        }
        payload
    }

    #[test]
    fn decodes_the_python_vei3_contract() {
        let decoded = decode_energy_inputs(&payload(3));
        assert!(decoded.is_ok());
        let Ok(decoded) = decoded else {
            return;
        };
        assert_eq!(decoded.sequence, 7);
        assert_eq!(decoded.grid_power_w.value, Some(-250.0));
        assert_eq!(decoded.battery_net_power_w.status, MeasurementStatus::Stale);
        assert_eq!(decoded.battery_capacity_wh.value, None);
        assert_eq!(
            decoded.battery_capacity_wh.status,
            MeasurementStatus::Unknown
        );
    }

    #[test]
    fn decodes_the_python_vei4_capacity_contract() {
        let decoded = decode_energy_inputs(&payload(4));
        assert!(decoded.is_ok());
        let Ok(decoded) = decoded else {
            return;
        };
        assert_eq!(decoded.battery_capacity_wh.value, Some(5_120.0));
        assert_eq!(decoded.battery_capacity_ah.value, Some(100.0));
        assert_eq!(decoded.battery_voltage_v.value, Some(52.8));
    }

    #[test]
    fn rejects_trailing_and_non_finite_values() {
        let mut trailing = payload(4);
        trailing.push(0);
        assert!(decode_energy_inputs(&trailing).is_err());

        let mut invalid = payload(4);
        let first_value_offset = 4 + 1 + 8 + 8 + 8 + 8 + 2;
        invalid[first_value_offset..first_value_offset + 8]
            .copy_from_slice(&f64::NAN.to_bits().to_be_bytes());
        assert!(decode_energy_inputs(&invalid).is_err());
    }
}
