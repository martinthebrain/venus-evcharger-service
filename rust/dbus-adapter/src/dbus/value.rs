// SPDX-License-Identifier: GPL-3.0-or-later
//! Exact scalar conversion used by the Victron `BusItem` interface.

use serde_json::Value as JsonValue;
use zbus::zvariant::{Array, OwnedValue, Str, Type, Value};

#[derive(Clone, Debug, PartialEq)]
pub enum BusValue {
    Invalid,
    Bool(bool),
    I32(i32),
    I64(i64),
    F64(f64),
    Text(String),
}

impl BusValue {
    pub fn from_json(value: &JsonValue) -> Result<Self, String> {
        match value {
            JsonValue::Null => Ok(Self::Invalid),
            JsonValue::Bool(item) => Ok(Self::Bool(*item)),
            JsonValue::Number(item) => number_from_json(item),
            JsonValue::String(item) => Ok(Self::Text(item.clone())),
            JsonValue::Array(_) | JsonValue::Object(_) => {
                Err("publication values must be scalar".to_owned())
            }
        }
    }

    pub fn from_owned(value: &OwnedValue) -> Result<Self, String> {
        match &**value {
            Value::U8(item) => Ok(Self::I32(i32::from(*item))),
            Value::Bool(item) => Ok(Self::Bool(*item)),
            Value::I16(item) => Ok(Self::I32(i32::from(*item))),
            Value::U16(item) => Ok(Self::I32(i32::from(*item))),
            Value::I32(item) => Ok(Self::I32(*item)),
            Value::U32(item) => i32::try_from(*item)
                .map(Self::I32)
                .or_else(|_| Ok(Self::I64(i64::from(*item)))),
            Value::I64(item) => Ok(Self::I64(*item)),
            Value::U64(item) => i64::try_from(*item)
                .map(Self::I64)
                .map_err(|_| "unsigned D-Bus integer exceeds i64".to_owned()),
            Value::F64(item) if item.is_finite() => Ok(Self::F64(*item)),
            Value::F64(_) => Err("D-Bus number must be finite".to_owned()),
            Value::Str(item) => Ok(Self::Text(item.as_str().to_owned())),
            Value::Array(item) if item.is_empty() => Ok(Self::Invalid),
            _ => Err("unsupported D-Bus value type".to_owned()),
        }
    }

    pub fn to_owned(&self) -> Result<OwnedValue, String> {
        match self {
            Self::Invalid => invalid_value(),
            Self::Bool(value) => Ok(OwnedValue::from(*value)),
            Self::I32(value) => Ok(OwnedValue::from(*value)),
            Self::I64(value) => Ok(OwnedValue::from(*value)),
            Self::F64(value) if value.is_finite() => Ok(OwnedValue::from(*value)),
            Self::F64(_) => Err("D-Bus number must be finite".to_owned()),
            Self::Text(value) => Ok(OwnedValue::from(Str::from(value.as_str()))),
        }
    }

    pub fn to_json(&self) -> JsonValue {
        match self {
            Self::Invalid => JsonValue::Null,
            Self::Bool(value) => JsonValue::Bool(*value),
            Self::I32(value) => JsonValue::from(*value),
            Self::I64(value) => JsonValue::from(*value),
            Self::F64(value) => JsonValue::from(*value),
            Self::Text(value) => JsonValue::String(value.clone()),
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Self::Bool(value) => Some(f64::from(u8::from(*value))),
            Self::I32(value) => Some(f64::from(*value)),
            Self::I64(value) => value.to_string().parse::<f64>().ok(),
            Self::F64(value) if value.is_finite() => Some(*value),
            Self::Text(value) => value.parse::<f64>().ok().filter(|item| item.is_finite()),
            Self::Invalid | Self::F64(_) => None,
        }
    }

    pub fn text(&self, path: &str, format: TextFormat) -> String {
        if self == &Self::Invalid {
            return "---".to_owned();
        }
        match format {
            TextFormat::Default if path == "/ProductId" => self
                .integer()
                .map_or_else(|| self.default_text(), |value| format!("0x{value:X}")),
            TextFormat::Default => self.default_text(),
            TextFormat::Kwh => format!("{:.2} kWh", self.number()),
            TextFormat::Amps => format!("{:.1} A", self.number()),
            TextFormat::Watts => format!("{:.1} W", self.number()),
            TextFormat::Volts => format!("{:.1} V", self.number()),
            TextFormat::Status => status_text(self.integer()),
        }
    }

    fn default_text(&self) -> String {
        match self {
            Self::Invalid => "---".to_owned(),
            Self::Bool(value) => value.to_string(),
            Self::I32(value) => value.to_string(),
            Self::I64(value) => value.to_string(),
            Self::F64(value) => value.to_string(),
            Self::Text(value) => value.clone(),
        }
    }

    fn number(&self) -> f64 {
        match self {
            Self::Bool(value) => f64::from(u8::from(*value)),
            Self::I32(value) => f64::from(*value),
            Self::I64(value) => value.to_string().parse::<f64>().unwrap_or(0.0),
            Self::F64(value) => *value,
            Self::Invalid | Self::Text(_) => 0.0,
        }
    }

    fn integer(&self) -> Option<i64> {
        match self {
            Self::Bool(value) => Some(i64::from(u8::from(*value))),
            Self::I32(value) => Some(i64::from(*value)),
            Self::I64(value) => Some(*value),
            Self::F64(value) if value.is_finite() => value.trunc().to_string().parse::<i64>().ok(),
            Self::Invalid | Self::F64(_) | Self::Text(_) => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum TextFormat {
    #[default]
    Default,
    Kwh,
    Amps,
    Watts,
    Volts,
    Status,
}

fn number_from_json(value: &serde_json::Number) -> Result<BusValue, String> {
    if let Some(integer) = value.as_i64() {
        return Ok(i32::try_from(integer).map_or(BusValue::I64(integer), BusValue::I32));
    }
    let number = value
        .as_f64()
        .filter(|number| number.is_finite())
        .ok_or_else(|| "publication number must be finite".to_owned())?;
    Ok(BusValue::F64(number))
}

fn invalid_value() -> Result<OwnedValue, String> {
    let array = Array::new(i32::SIGNATURE);
    OwnedValue::try_from(array).map_err(|error| error.to_string())
}

fn status_text(value: Option<i64>) -> String {
    match value {
        Some(0) => "Getrennt",
        Some(1) => "Bereit",
        Some(2) => "Laden",
        Some(3) => "Fertig",
        Some(4) => "Warten auf PV",
        Some(6) => "Warten auf Start",
        _ => "Unbekannt",
    }
    .to_owned()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{BusValue, TextFormat};

    #[test]
    fn json_scalars_keep_victron_numeric_widths() {
        assert_eq!(BusValue::from_json(&json!(42)), Ok(BusValue::I32(42)));
        assert_eq!(
            BusValue::from_json(&json!(4_294_967_296_i64)),
            Ok(BusValue::I64(4_294_967_296)),
        );
        assert_eq!(BusValue::from_json(&json!(12.5)), Ok(BusValue::F64(12.5)));
        assert!(BusValue::from_json(&json!({"not": "scalar"})).is_err());
    }

    #[test]
    fn invalid_value_is_an_empty_signed_integer_array() -> Result<(), String> {
        let owned = BusValue::Invalid.to_owned()?;
        assert_eq!(BusValue::from_owned(&owned), Ok(BusValue::Invalid));
        Ok(())
    }

    #[test]
    fn formatters_match_vedbus_surface() {
        assert_eq!(
            BusValue::F64(12.345).text("/Energy", TextFormat::Kwh),
            "12.35 kWh"
        );
        assert_eq!(
            BusValue::I32(4).text("/Status", TextFormat::Status),
            "Warten auf PV"
        );
        assert_eq!(
            BusValue::I32(0xffff).text("/ProductId", TextFormat::Default),
            "0xFFFF"
        );
        assert_eq!(BusValue::Invalid.text("/Soc", TextFormat::Default), "---");
    }
}
