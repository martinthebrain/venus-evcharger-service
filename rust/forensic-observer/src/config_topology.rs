//! Normalized topology parsing for observer diagnostics.

use std::path::{Path, PathBuf};

use crate::config::{BackendSelection, DEFAULT_METER_TYPE, MAX_CONFIG_BYTES, ObserverConfig};
use crate::error::{ObserverError, Result};
use crate::ini::{IniDocument, read_bounded_text};

pub fn topology_selection(config: &ObserverConfig) -> Result<BackendSelection> {
    let topology_type = required_choice(
        &config.ini,
        "Topology",
        "Type",
        &[
            "simple_relay",
            "native_device",
            "hybrid_topology",
            "custom_topology",
        ],
    )?;
    let switch_type = optional_role(
        &config.ini,
        "Actuator",
        &[
            "cerbo_gx_relay_switch",
            "shelly_switch",
            "shelly_contactor_switch",
            "template_switch",
            "tasmota_switch",
            "tasmota_contactor_switch",
            "tuya_switch",
            "tuya_contactor_switch",
            "switch_group",
            "custom",
        ],
    )?;
    let charger_type = optional_role(
        &config.ini,
        "Charger",
        &[
            "goe_charger",
            "simpleevse_charger",
            "smartevse_charger",
            "modbus_charger",
            "template_charger",
            "custom",
        ],
    )?;
    validate_topology_roles(
        &topology_type,
        switch_type.as_deref(),
        charger_type.as_deref(),
    )?;
    let switch_path = section_path(&config.ini, "Actuator");
    let charger_path = section_path(&config.ini, "Charger");
    let (meter_type, meter_path, measurement_type) =
        topology_measurement(config, switch_type.as_deref(), charger_type.as_deref())?;
    validate_policy(config, measurement_type.as_deref())?;
    Ok(BackendSelection {
        mode: "split".to_owned(),
        meter_type,
        switch_type,
        charger_type,
        meter_config_path: meter_path,
        switch_config_path: switch_path,
        charger_config_path: charger_path,
    })
}

fn topology_measurement(
    config: &ObserverConfig,
    switch_type: Option<&str>,
    charger_type: Option<&str>,
) -> Result<(Option<String>, Option<PathBuf>, Option<String>)> {
    if !config.ini.has_section("Measurement") {
        return Ok((None, None, None));
    }
    let measurement_type = required_choice(
        &config.ini,
        "Measurement",
        "Type",
        &[
            "actuator_native",
            "charger_native",
            "external_meter",
            "fixed_reference",
            "learned_reference",
            "none",
        ],
    )?;
    let path = section_path(&config.ini, "Measurement");
    let roles = match measurement_type.as_str() {
        "external_meter" => {
            let adapter_path = path.ok_or_else(|| {
                ObserverError::Configuration(
                    "external_meter requires Measurement.ConfigPath".to_owned(),
                )
            })?;
            let adapter_type = adapter_type_from_path(config, &adapter_path)?;
            (adapter_type, Some(adapter_path))
        }
        "actuator_native" => {
            if switch_type.is_none() {
                return Err(ObserverError::Configuration(
                    "actuator_native measurement requires an actuator".to_owned(),
                ));
            }
            let meter = switch_type
                .filter(|value| matches!(*value, "shelly_switch" | "shelly_contactor_switch"))
                .map(|_| DEFAULT_METER_TYPE.to_owned());
            (meter, None)
        }
        "charger_native" => {
            if charger_type.is_none() {
                return Err(ObserverError::Configuration(
                    "charger_native measurement requires a charger".to_owned(),
                ));
            }
            (None, None)
        }
        "fixed_reference" => {
            validate_reference_watts(&config.ini)?;
            (None, None)
        }
        _ => (None, None),
    };
    Ok((roles.0, roles.1, Some(measurement_type)))
}

fn validate_reference_watts(ini: &IniDocument) -> Result<()> {
    let value = ini
        .get("Measurement", "ReferenceWatts")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            ObserverError::Configuration(
                "fixed_reference requires Measurement.ReferenceWatts".to_owned(),
            )
        })?;
    value.parse::<f64>().map(|_value| ()).map_err(|error| {
        ObserverError::Configuration(format!(
            "Measurement.ReferenceWatts must be numeric: {error}"
        ))
    })
}

fn validate_policy(config: &ObserverConfig, measurement_type: Option<&str>) -> Result<()> {
    if !config.ini.has_section("Policy") {
        return Ok(());
    }
    let mode = config
        .ini
        .get("Policy", "Mode")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("manual")
        .to_ascii_lowercase();
    if !["manual", "auto", "scheduled"].contains(&mode.as_str()) {
        return Err(ObserverError::Configuration(format!(
            "invalid Policy.Mode: {mode:?}"
        )));
    }
    if mode == "auto" && measurement_type == Some("none") {
        return Err(ObserverError::Configuration(
            "auto policy requires a non-empty measurement mode".to_owned(),
        ));
    }
    Ok(())
}

fn adapter_type_from_path(config: &ObserverConfig, path: &Path) -> Result<Option<String>> {
    let resolved = resolve_relative(&config.path, path);
    let Ok(text) = read_bounded_text(&resolved, MAX_CONFIG_BYTES, "backend config") else {
        return Ok(None);
    };
    let adapter = IniDocument::parse(&text)?;
    Ok(adapter
        .get_case_insensitive("Adapter", "Type")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase))
}

fn required_choice(
    ini: &IniDocument,
    section: &str,
    key: &str,
    choices: &[&str],
) -> Result<String> {
    let raw = ini
        .get(section, key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            ObserverError::Configuration(format!("missing required key {section}.{key}"))
        })?;
    let normalized = raw.to_ascii_lowercase();
    if choices.contains(&normalized.as_str()) {
        Ok(normalized)
    } else {
        Err(ObserverError::Configuration(format!(
            "invalid {section}.{key}: {raw:?}"
        )))
    }
}

fn optional_role(ini: &IniDocument, section: &str, choices: &[&str]) -> Result<Option<String>> {
    if !ini.has_section(section) {
        return Ok(None);
    }
    required_choice(ini, section, "Type", choices).map(Some)
}

fn validate_topology_roles(
    topology: &str,
    switch: Option<&str>,
    charger: Option<&str>,
) -> Result<()> {
    match topology {
        "simple_relay" if switch.is_none() => Err(ObserverError::Configuration(
            "simple_relay requires an actuator".to_owned(),
        )),
        "native_device" if charger.is_none() => Err(ObserverError::Configuration(
            "native_device requires a charger".to_owned(),
        )),
        "hybrid_topology" if switch.is_none() || charger.is_none() => {
            Err(ObserverError::Configuration(
                "hybrid_topology requires both charger and actuator".to_owned(),
            ))
        }
        _ => Ok(()),
    }
}

fn section_path(ini: &IniDocument, section: &str) -> Option<PathBuf> {
    ini.get(section, "ConfigPath")
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn resolve_relative(main_config_path: &Path, backend_path: &Path) -> PathBuf {
    if backend_path.is_absolute() {
        backend_path.to_path_buf()
    } else {
        main_config_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(backend_path)
    }
}
