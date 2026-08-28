// SPDX-License-Identifier: GPL-3.0-or-later
//! Validation and wire-value helpers for semantic gateway commands.

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use quick_xml::Reader;
use quick_xml::events::Event;
use serde_json::{Map, Value};

use crate::broker::{DbusResult, DbusResultValue};
use crate::dbus::BusValue;

const MAX_INTROSPECTION_BYTES: usize = 256 * 1024;

pub(super) fn relay_index(command: &Map<String, Value>) -> Result<usize, String> {
    command
        .get("relay_index")
        .and_then(Value::as_u64)
        .filter(|value| matches!(value, 0 | 1))
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| "GX relay index must be 0 or 1".to_owned())
}

pub(super) fn relay_target(command: &Map<String, Value>) -> Result<i32, String> {
    let enabled = strict_bool(command, "enabled")?;
    let mode = command
        .get("contact_mode")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "NO" | "NC"))
        .ok_or_else(|| "GX relay contact mode must be NO or NC".to_owned())?;
    Ok(i32::from(if mode == "NO" { enabled } else { !enabled }))
}

pub(super) fn strict_bool(command: &Map<String, Value>, field: &str) -> Result<bool, String> {
    command
        .get(field)
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("{field} must be boolean"))
}

pub(super) fn finite_number(command: &Map<String, Value>, field: &str) -> Result<f64, String> {
    command
        .get(field)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("{field} must be finite"))
}

pub(super) fn nonnegative_number(command: &Map<String, Value>, field: &str) -> Result<f64, String> {
    finite_number(command, field).and_then(|value| {
        if value >= 0.0 {
            Ok(value)
        } else {
            Err(format!("{field} must be non-negative"))
        }
    })
}

pub(super) fn command_delay(command: &Map<String, Value>) -> Result<Instant, String> {
    let requested = command
        .get("not_before")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    if !requested.is_finite() || requested < 0.0 {
        return Err("not_before must be finite and non-negative".to_owned());
    }
    let now_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_secs_f64();
    Ok(Instant::now() + Duration::from_secs_f64((requested - now_epoch).max(0.0)))
}

pub(super) fn relay_state_path(relay: usize) -> String {
    format!("/Relay/{relay}/State")
}

pub(super) fn manual_paths(relay: usize) -> Vec<String> {
    let primary = format!("/Settings/Relay/{relay}/Function");
    if relay == 0 {
        vec![primary, "/Settings/Relay/Function".to_owned()]
    } else {
        vec![primary]
    }
}

pub(super) fn binary_value(response: &DbusResult) -> Result<Option<i32>, String> {
    Ok(match numeric_value(response)? {
        Some(value) if value.to_bits() == 0.0_f64.to_bits() => Some(0),
        Some(value) if value.to_bits() == 1.0_f64.to_bits() => Some(1),
        _ => None,
    })
}

pub(super) fn numeric_value(response: &DbusResult) -> Result<Option<f64>, String> {
    match response.result.as_ref().map_err(Clone::clone)? {
        DbusResultValue::Value(value) => Ok(value.as_f64()),
        _ => Err("D-Bus operation did not return a value".to_owned()),
    }
}

pub(super) fn string_value(response: &DbusResult) -> Result<&str, String> {
    match response.result.as_ref().map_err(Clone::clone)? {
        DbusResultValue::Value(BusValue::Text(value)) => Ok(value),
        _ => Err("D-Bus operation did not return text".to_owned()),
    }
}

pub(super) fn xml_value(response: &DbusResult) -> Result<&str, String> {
    match response.result.as_ref().map_err(Clone::clone)? {
        DbusResultValue::Xml(value) => Ok(value),
        _ => Err("D-Bus introspection did not return XML".to_owned()),
    }
}

pub(super) fn write_code(response: &DbusResult) -> Result<i32, String> {
    match response.result.as_ref().map_err(Clone::clone)? {
        DbusResultValue::WriteCode(value) => Ok(*value),
        _ => Err("D-Bus operation did not return a write code".to_owned()),
    }
}

pub(super) fn selector(command: &Map<String, Value>) -> Result<(String, String), String> {
    let payload = command
        .get("selector")
        .and_then(Value::as_object)
        .ok_or_else(|| "generic Shelly selector is invalid".to_owned())?;
    let kind = payload
        .get("kind")
        .and_then(Value::as_str)
        .filter(|value| matches!(*value, "ip" | "mac"))
        .ok_or_else(|| "generic Shelly selector kind is invalid".to_owned())?;
    let raw = payload
        .get("value")
        .and_then(Value::as_str)
        .ok_or_else(|| "generic Shelly selector value is invalid".to_owned())?;
    let value = if kind == "mac" {
        normalize_mac(raw).ok_or_else(|| "generic Shelly MAC is invalid".to_owned())?
    } else {
        raw.to_owned()
    };
    Ok((kind.to_owned(), value))
}

pub(super) fn channel(command: &Map<String, Value>) -> Result<i64, String> {
    command
        .get("channel")
        .and_then(Value::as_i64)
        .filter(|value| *value >= 0)
        .ok_or_else(|| "generic Shelly channel is invalid".to_owned())
}

pub(super) fn shelly_enabled_path(device: &str, channel: i64) -> String {
    format!("/Devices/{device}/{channel}/Enabled")
}

pub(super) fn normalize_mac(value: &str) -> Option<String> {
    let normalized = value
        .chars()
        .filter(char::is_ascii_hexdigit)
        .map(|character| character.to_ascii_uppercase())
        .collect::<String>();
    (normalized.len() == 12).then_some(normalized)
}

pub(super) fn device_nodes(xml: &str) -> Result<Vec<String>, String> {
    if xml.len() > MAX_INTROSPECTION_BYTES {
        return Err("generic Shelly introspection XML is too large".to_owned());
    }
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut result = Vec::new();
    loop {
        match reader.read_event() {
            Ok(Event::Start(node) | Event::Empty(node)) if node.name().as_ref() == b"node" => {
                for attribute in node.attributes().with_checks(true) {
                    let attribute = attribute.map_err(|error| error.to_string())?;
                    if attribute.key.as_ref() == b"name" {
                        let name = attribute
                            .decode_and_unescape_value(reader.decoder())
                            .map_err(|error| error.to_string())?
                            .trim()
                            .to_owned();
                        if !name.is_empty() && !name.contains('/') && result.len() < 256 {
                            result.push(name);
                        }
                    }
                }
            }
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(error) => return Err(error.to_string()),
        }
    }
    result.sort();
    result.dedup();
    Ok(result)
}

#[cfg(test)]
mod tests {
    use serde_json::{Map, json};

    use super::{device_nodes, normalize_mac, relay_target};

    #[test]
    fn relay_contact_mode_contract_is_exact() -> Result<(), String> {
        let no: Map<String, _> =
            serde_json::from_value(json!({"enabled":true,"contact_mode":"NO"}))
                .map_err(|error| error.to_string())?;
        let nc: Map<String, _> =
            serde_json::from_value(json!({"enabled":true,"contact_mode":"NC"}))
                .map_err(|error| error.to_string())?;
        assert_eq!(relay_target(&no), Ok(1));
        assert_eq!(relay_target(&nc), Ok(0));
        Ok(())
    }

    #[test]
    fn shelly_xml_is_bounded_and_names_are_normalized() -> Result<(), String> {
        let nodes =
            device_nodes("<node><node name='AABBCCDDEEFF'/><node name='device-2'/></node>")?;
        assert_eq!(nodes, ["AABBCCDDEEFF", "device-2"]);
        assert_eq!(
            normalize_mac("aa:bb:cc:dd:ee:ff").as_deref(),
            Some("AABBCCDDEEFF")
        );
        Ok(())
    }
}
