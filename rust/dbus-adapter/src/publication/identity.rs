// SPDX-License-Identifier: GPL-3.0-or-later
//! Stable Victron service identities for EVCS and companion publications.

use serde_json::{Map, Value};

use crate::config::IniConfig;
use crate::dbus::{BusValue, TextFormat};

pub(super) fn evcs_identity_paths(
    config: &IniConfig,
    identity: &Map<String, Value>,
    device_instance: i64,
) -> Result<Vec<(String, BusValue, TextFormat, bool)>, String> {
    let mut paths = common_identity_paths(identity, device_instance)?;
    paths.push((
        "/Position".to_owned(),
        integer_bus_value(config.i64("Position", 1)),
        TextFormat::Default,
        false,
    ));
    Ok(paths)
}

pub(super) fn companion_identity_paths(
    identity: &Map<String, Value>,
    device_instance: i64,
) -> Result<Vec<(String, BusValue, TextFormat, bool)>, String> {
    common_identity_paths(identity, device_instance)
}

fn common_identity_paths(
    identity: &Map<String, Value>,
    device_instance: i64,
) -> Result<Vec<(String, BusValue, TextFormat, bool)>, String> {
    let mappings = [
        ("process_name", "/Mgmt/ProcessName"),
        ("process_version", "/Mgmt/ProcessVersion"),
        ("connection_name", "/Mgmt/Connection"),
        ("product_name", "/ProductName"),
        ("custom_name", "/CustomName"),
        ("firmware_version", "/FirmwareVersion"),
        ("hardware_version", "/HardwareVersion"),
        ("serial", "/Serial"),
    ];
    let mut paths = Vec::new();
    for (field, path) in mappings {
        let value =
            text(identity, field).ok_or_else(|| format!("publication identity lacks {field}"))?;
        paths.push((
            path.to_owned(),
            BusValue::Text(value.to_owned()),
            TextFormat::Default,
            false,
        ));
    }
    paths.push((
        "/DeviceInstance".to_owned(),
        integer_bus_value(device_instance),
        TextFormat::Default,
        false,
    ));
    paths.push((
        "/ProductId".to_owned(),
        BusValue::I32(0xffff),
        TextFormat::Default,
        false,
    ));
    Ok(paths)
}

pub(super) fn companion_identity(
    config: &IniConfig,
    kind: &str,
    service_id: &str,
) -> (String, i64) {
    let token = if kind == "pv_inverter" {
        "pvinverter"
    } else {
        kind
    };
    let base = config.i64("DeviceInstance", 60);
    let aggregate_kind = match service_id {
        "aggregate-battery" => Some("battery"),
        "aggregate-pv" => Some("pv_inverter"),
        "aggregate-grid" => Some("grid"),
        _ => None,
    };
    if aggregate_kind == Some(kind) {
        let offset = match kind {
            "battery" => 40,
            "pv_inverter" => 41,
            _ => 42,
        };
        let instance_key = format!("Companion{}DeviceInstance", title_kind(kind));
        let instance = config.i64(&instance_key, base + offset);
        let name_key = format!("Companion{}ServiceName", title_kind(kind));
        let fallback = format!("com.victronenergy.{token}.external_{instance}");
        return (configured_text(config, &name_key, &fallback), instance);
    }
    let source_offset = match kind {
        "battery" => 140,
        "pv_inverter" => 240,
        _ => 340,
    };
    let instance_key = format!("CompanionSource{}DeviceInstanceBase", title_kind(kind));
    let configured_base = config.i64(&instance_key, base + source_offset);
    let digest = crc32(service_id.as_bytes());
    let instance = configured_base + i64::from(digest % 90);
    let prefix_key = format!("CompanionSource{}ServicePrefix", title_kind(kind));
    let fallback = format!("com.victronenergy.{token}.external");
    let prefix = configured_text(config, &prefix_key, &fallback)
        .trim_end_matches('.')
        .to_owned();
    (
        format!("{prefix}.{}_{digest:08x}", sanitized_service_id(service_id)),
        instance,
    )
}

fn configured_text(config: &IniConfig, key: &str, fallback: &str) -> String {
    let value = config.text(key, fallback);
    if value.is_empty() {
        fallback.to_owned()
    } else {
        value
    }
}

fn text<'a>(payload: &'a Map<String, Value>, field: &str) -> Option<&'a str> {
    payload.get(field).and_then(Value::as_str)
}

fn integer_bus_value(value: i64) -> BusValue {
    i32::try_from(value).map_or(BusValue::I64(value), BusValue::I32)
}

fn title_kind(kind: &str) -> &str {
    match kind {
        "battery" => "Battery",
        "pv_inverter" => "PvInverter",
        _ => "Grid",
    }
}

fn sanitized_service_id(value: &str) -> String {
    let mut result = String::new();
    let mut separator = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() {
            if separator && !result.is_empty() {
                result.push('_');
            }
            result.push(character.to_ascii_lowercase());
            separator = false;
        } else {
            separator = true;
        }
        if result.len() >= 48 {
            break;
        }
    }
    if result.is_empty() {
        "source".to_owned()
    } else {
        result
    }
}

fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = 0xffff_ffff_u32;
    for byte in bytes {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb8_8320_u32 & (0_u32.wrapping_sub(crc & 1)));
        }
    }
    !crc
}

pub(super) fn identity_path_count(kind: &str) -> usize {
    if kind == "evcs" { 11 } else { 10 }
}

#[cfg(test)]
mod tests {
    use super::{companion_identity, crc32, sanitized_service_id};
    use crate::config::IniConfig;

    #[test]
    fn companion_identity_matches_python_crc32_contract() -> Result<(), String> {
        assert_eq!(crc32(b"source one"), 0x18c4_337a);
        assert_eq!(sanitized_service_id("Source One"), "source_one");
        let config = IniConfig::parse("[DEFAULT]\nDeviceInstance=60\n")?;
        let (name, instance) = companion_identity(&config, "battery", "source one");
        assert_eq!(
            name,
            "com.victronenergy.battery.external.source_one_18c4337a"
        );
        assert_eq!(instance, 200 + i64::from(0x18c4_337a_u32 % 90));
        Ok(())
    }
}
