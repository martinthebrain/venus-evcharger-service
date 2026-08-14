//! Normalized observer configuration and backend-role selection.

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::{ObserverError, Result};
use crate::ini::{IniDocument, read_bounded_text};

pub(super) const MAX_CONFIG_BYTES: u64 = 1_048_576;
const DEFAULT_DEVICE_INSTANCE: u32 = 60;
const DEFAULT_GATEWAY_DIAGNOSTICS_PATH: &str = "/run/venus-evcharger/gateway-diagnostics.json";
pub(super) const DEFAULT_METER_TYPE: &str = "shelly_meter";
const DEFAULT_SWITCH_TYPE: &str = "shelly_contactor_switch";

/// Observer-owned configuration and derived runtime paths.
#[derive(Clone, Debug)]
pub struct ObserverConfig {
    /// Main configuration path.
    pub path: PathBuf,
    /// Original text retained for redacted artifacts.
    pub source_text: String,
    /// Parsed INI document.
    pub ini: IniDocument,
}

impl ObserverConfig {
    /// Load one bounded main configuration file.
    ///
    /// # Errors
    ///
    /// Returns an error when the file cannot be read within its size bound or
    /// violates the INI syntax contract.
    pub fn load(path: &Path) -> Result<Self> {
        let source_text = read_bounded_text(path, MAX_CONFIG_BYTES, "observer config")?;
        let ini = IniDocument::parse(&source_text)?;
        Ok(Self {
            path: path.to_path_buf(),
            source_text,
            ini,
        })
    }

    /// Return the configured device instance or the stable fallback.
    #[must_use]
    pub fn device_instance(&self) -> u32 {
        self.ini
            .default_value("DeviceInstance")
            .and_then(|value| value.trim().parse::<u32>().ok())
            .unwrap_or(DEFAULT_DEVICE_INSTANCE)
    }

    /// Return the semantic gateway-diagnostics transport path.
    #[must_use]
    pub fn gateway_diagnostics_path(&self) -> PathBuf {
        configured_path(
            self.ini.default_value("GatewayDiagnosticsSnapshotPath"),
            DEFAULT_GATEWAY_DIAGNOSTICS_PATH,
        )
    }

    /// Return the auto-input snapshot path.
    #[must_use]
    pub fn auto_input_snapshot_path(&self) -> PathBuf {
        self.ini
            .default_value("AutoInputSnapshotPath")
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map_or_else(
                || {
                    PathBuf::from(format!(
                        "/run/dbus-venus-evcharger-auto-{}.json",
                        self.device_instance()
                    ))
                },
                PathBuf::from,
            )
    }

    /// Return the core runtime-state path.
    #[must_use]
    pub fn runtime_state_path(&self) -> PathBuf {
        PathBuf::from(format!(
            "/run/dbus-venus-evcharger-{}.json",
            self.device_instance()
        ))
    }

    /// Normalize backend roles for diagnostics and optional probes.
    ///
    /// # Errors
    ///
    /// Returns an error when configured topology roles contradict each other
    /// or use unsupported values.
    pub fn backend_selection(&self) -> Result<BackendSelection> {
        if self.ini.has_section("Topology") {
            crate::config_topology::topology_selection(self)
        } else {
            legacy_selection(self)
        }
    }
}

/// Normalized backend selection used by observer diagnostics.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BackendSelection {
    /// Combined or split backend mode.
    pub mode: String,
    /// Selected meter backend, if any.
    pub meter_type: Option<String>,
    /// Selected switch backend, if any.
    pub switch_type: Option<String>,
    /// Selected charger backend, if any.
    pub charger_type: Option<String>,
    /// Optional meter adapter configuration.
    pub meter_config_path: Option<PathBuf>,
    /// Optional switch adapter configuration.
    pub switch_config_path: Option<PathBuf>,
    /// Optional charger adapter configuration.
    pub charger_config_path: Option<PathBuf>,
}

/// JSON-ready backend selection preserving the Python artifact schema.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BackendSelectionPayload {
    mode: String,
    meter_type: String,
    switch_type: String,
    charger_type: Option<String>,
    meter_config_path: String,
    switch_config_path: String,
    charger_config_path: String,
}

impl BackendSelection {
    /// Project the normalized roles into their stable artifact representation.
    #[must_use]
    pub fn to_payload(&self) -> BackendSelectionPayload {
        BackendSelectionPayload {
            mode: self.mode.clone(),
            meter_type: self.meter_type.clone().unwrap_or_else(|| "none".to_owned()),
            switch_type: self
                .switch_type
                .clone()
                .unwrap_or_else(|| "none".to_owned()),
            charger_type: self.charger_type.clone(),
            meter_config_path: path_text(self.meter_config_path.as_deref()),
            switch_config_path: path_text(self.switch_config_path.as_deref()),
            charger_config_path: path_text(self.charger_config_path.as_deref()),
        }
    }

    /// Return the selected backend type and config path for one probe role.
    #[must_use]
    pub fn role(&self, role: &str) -> Option<(&str, Option<&Path>)> {
        match role {
            "meter" => Some((
                self.meter_type.as_deref().unwrap_or(""),
                self.meter_config_path.as_deref(),
            )),
            "switch" => Some((
                self.switch_type.as_deref().unwrap_or(""),
                self.switch_config_path.as_deref(),
            )),
            "charger" => Some((
                self.charger_type.as_deref().unwrap_or(""),
                self.charger_config_path.as_deref(),
            )),
            _ => None,
        }
    }
}

fn configured_path(value: Option<&str>, fallback: &str) -> PathBuf {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map_or_else(|| PathBuf::from(fallback), PathBuf::from)
}

fn legacy_selection(config: &ObserverConfig) -> Result<BackendSelection> {
    let section = if config.ini.has_section("Backends") {
        "Backends"
    } else {
        "DEFAULT"
    };
    let mode = match config
        .ini
        .get(section, "Mode")
        .map(str::trim)
        .map(str::to_ascii_lowercase)
    {
        Some(value) if value == "split" => "split",
        _ => "combined",
    };
    let charger_type = optional_lower(config.ini.get(section, "ChargerType"));
    validate_charger_type(charger_type.as_deref())?;
    let meter_type = normalized_legacy_role(
        mode,
        config.ini.get(section, "MeterType"),
        DEFAULT_METER_TYPE,
        charger_type.as_deref(),
        "MeterType",
    )?;
    let switch_type = normalized_legacy_role(
        mode,
        config.ini.get(section, "SwitchType"),
        DEFAULT_SWITCH_TYPE,
        charger_type.as_deref(),
        "SwitchType",
    )?;
    Ok(BackendSelection {
        mode: mode.to_owned(),
        meter_type,
        switch_type,
        charger_type,
        meter_config_path: optional_path(config.ini.get(section, "MeterConfigPath")),
        switch_config_path: optional_path(config.ini.get(section, "SwitchConfigPath")),
        charger_config_path: optional_path(config.ini.get(section, "ChargerConfigPath")),
    })
}

fn normalized_legacy_role(
    mode: &str,
    value: Option<&str>,
    fallback: &str,
    charger_type: Option<&str>,
    field: &str,
) -> Result<Option<String>> {
    let normalized = value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map_or_else(|| fallback.to_owned(), str::to_ascii_lowercase);
    if normalized == "none" {
        if mode != "split" {
            return Err(ObserverError::Configuration(format!(
                "{field}=none is only supported in split backend mode"
            )));
        }
        if charger_type.is_none() {
            return Err(ObserverError::Configuration(format!(
                "{field}=none requires a configured charger backend"
            )));
        }
        return Ok(None);
    }
    if normalized == "shelly_combined" {
        return Ok(Some(fallback.to_owned()));
    }
    Ok(Some(normalized))
}

fn validate_charger_type(value: Option<&str>) -> Result<()> {
    let Some(charger) = value else {
        return Ok(());
    };
    if [
        "goe_charger",
        "simpleevse_charger",
        "smartevse_charger",
        "modbus_charger",
        "template_charger",
        "custom",
    ]
    .contains(&charger)
    {
        return Ok(());
    }
    Err(ObserverError::Configuration(format!(
        "invalid Charger.Type: {charger:?}"
    )))
}

fn optional_lower(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase)
}

fn optional_path(value: Option<&str>) -> Option<PathBuf> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn path_text(path: Option<&Path>) -> String {
    path.map_or_else(String::new, |value| value.to_string_lossy().into_owned())
}
