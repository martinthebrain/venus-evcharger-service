// SPDX-License-Identifier: GPL-3.0-or-later
//! Case-preserving configuration boundary for the native gateway.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

const DEFAULT_RUN_DIR: &str = "/run/venus-evcharger";

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct IniConfig {
    defaults: BTreeMap<String, String>,
    sections: BTreeMap<String, BTreeMap<String, String>>,
}

impl IniConfig {
    pub fn load(path: &Path) -> Result<Self, String> {
        let source = fs::read_to_string(path)
            .map_err(|error| format!("unable to read {}: {error}", path.display()))?;
        Self::parse(&source)
    }

    pub fn parse(source: &str) -> Result<Self, String> {
        let mut config = Self::default();
        let mut section = String::from("DEFAULT");
        for (index, raw_line) in source.lines().enumerate() {
            let line_number = index + 1;
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
                continue;
            }
            if line.starts_with('[') {
                section = parse_section(line, line_number)?;
                config.sections.entry(section.clone()).or_default();
                continue;
            }
            let (key, value) = parse_assignment(line, line_number)?;
            if section == "DEFAULT" {
                config.defaults.insert(key, value);
            } else {
                config
                    .sections
                    .entry(section.clone())
                    .or_default()
                    .insert(key, value);
            }
        }
        Ok(config)
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        case_insensitive_get(&self.defaults, key)
    }

    #[cfg(test)]
    pub fn section(&self, section: &str) -> Option<&BTreeMap<String, String>> {
        self.sections
            .iter()
            .find_map(|(name, values)| name.eq_ignore_ascii_case(section).then_some(values))
    }

    pub fn text(&self, key: &str, fallback: &str) -> String {
        self.get(key).map_or(fallback, str::trim).to_owned()
    }

    pub fn optional_text(&self, key: &str) -> Option<String> {
        self.get(key)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
    }

    pub fn bool(&self, key: &str, fallback: bool) -> bool {
        self.get(key).map_or(fallback, truthy)
    }

    pub fn f64(&self, key: &str, fallback: f64) -> f64 {
        self.get(key)
            .and_then(|value| value.trim().parse::<f64>().ok())
            .filter(|value| value.is_finite())
            .unwrap_or(fallback)
    }

    pub fn i64(&self, key: &str, fallback: i64) -> i64 {
        self.get(key)
            .and_then(|value| value.trim().parse::<i64>().ok())
            .unwrap_or(fallback)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GatewayPaths {
    pub run_dir: PathBuf,
    pub socket_path: PathBuf,
    pub cache_path: PathBuf,
    pub cache_sequence_path: PathBuf,
    pub health_path: PathBuf,
    pub health_history_path: PathBuf,
    pub command_lifecycle_path: PathBuf,
    pub diagnostics_path: PathBuf,
    pub introspection_path: PathBuf,
    pub energy_inputs_path: PathBuf,
    pub energy_topology_path: PathBuf,
    pub command_dir: PathBuf,
    pub core_command_dir: PathBuf,
    pub order_state_path: PathBuf,
}

impl GatewayPaths {
    pub fn from_config(
        config: &IniConfig,
        run_dir_override: Option<&Path>,
    ) -> Result<Self, String> {
        let run_dir = run_dir_override.map_or_else(
            || PathBuf::from(config.text("DbusGatewayRunDir", DEFAULT_RUN_DIR)),
            Path::to_path_buf,
        );
        require_absolute(&run_dir, "DbusGatewayRunDir")?;
        if run_dir_override.is_some() {
            return Ok(Self::for_run_dir(run_dir));
        }
        Ok(Self {
            socket_path: configured_path(
                config,
                "DbusGatewaySocketPath",
                run_dir.join("gateway.sock"),
            )?,
            cache_path: configured_path(
                config,
                "DbusGatewayCachePath",
                run_dir.join("dbus-cache.json"),
            )?,
            cache_sequence_path: run_dir.join("dbus-cache.seq"),
            health_path: configured_path(
                config,
                "DbusGatewayHealthPath",
                run_dir.join("dbus-health.json"),
            )?,
            health_history_path: configured_path(
                config,
                "DbusGatewayHealthLogPath",
                run_dir.join("dbus-health-history.jsonl"),
            )?,
            command_lifecycle_path: configured_path(
                config,
                "DbusGatewayCommandLifecyclePath",
                run_dir.join("dbus-command-lifecycle.jsonl"),
            )?,
            diagnostics_path: run_dir.join("gateway-diagnostics.json"),
            introspection_path: configured_path(
                config,
                "DbusIntrospectionSnapshotPath",
                run_dir.join("dbus-introspection.json"),
            )?,
            energy_inputs_path: run_dir.join("energy-inputs.v4.bin"),
            energy_topology_path: run_dir.join("energy-topology.json"),
            command_dir: configured_path(
                config,
                "DbusGatewayCommandDir",
                run_dir.join("dbus-commands"),
            )?,
            core_command_dir: configured_path(
                config,
                "DbusGatewayCoreCommandDir",
                run_dir.join("core-commands"),
            )?,
            order_state_path: run_dir.join("publication-order-state.json"),
            run_dir,
        })
    }

    fn for_run_dir(run_dir: PathBuf) -> Self {
        Self {
            socket_path: run_dir.join("gateway.sock"),
            cache_path: run_dir.join("dbus-cache.json"),
            cache_sequence_path: run_dir.join("dbus-cache.seq"),
            health_path: run_dir.join("dbus-health.json"),
            health_history_path: run_dir.join("dbus-health-history.jsonl"),
            command_lifecycle_path: run_dir.join("dbus-command-lifecycle.jsonl"),
            diagnostics_path: run_dir.join("gateway-diagnostics.json"),
            introspection_path: run_dir.join("dbus-introspection.json"),
            energy_inputs_path: run_dir.join("energy-inputs.v4.bin"),
            energy_topology_path: run_dir.join("energy-topology.json"),
            command_dir: run_dir.join("dbus-commands"),
            core_command_dir: run_dir.join("core-commands"),
            order_state_path: run_dir.join("publication-order-state.json"),
            run_dir,
        }
    }
}

fn parse_section(line: &str, line_number: usize) -> Result<String, String> {
    if !line.ends_with(']') {
        return Err(format!("unterminated section at line {line_number}"));
    }
    let name = line[1..line.len() - 1].trim();
    if name.is_empty() {
        return Err(format!("empty section at line {line_number}"));
    }
    Ok(name.to_owned())
}

fn parse_assignment(line: &str, line_number: usize) -> Result<(String, String), String> {
    let Some((raw_key, raw_value)) = line.split_once('=') else {
        return Err(format!("invalid assignment at line {line_number}"));
    };
    let key = raw_key.trim();
    if key.is_empty() {
        return Err(format!("empty option at line {line_number}"));
    }
    Ok((key.to_owned(), raw_value.trim().to_owned()))
}

fn case_insensitive_get<'a>(values: &'a BTreeMap<String, String>, key: &str) -> Option<&'a str> {
    values
        .iter()
        .find_map(|(name, value)| name.eq_ignore_ascii_case(key).then_some(value.as_str()))
}

fn truthy(value: &str) -> bool {
    ["1", "true", "yes", "on"]
        .iter()
        .any(|candidate| value.trim().eq_ignore_ascii_case(candidate))
}

fn configured_path(config: &IniConfig, key: &str, fallback: PathBuf) -> Result<PathBuf, String> {
    let path = config.optional_text(key).map_or(fallback, PathBuf::from);
    require_absolute(&path, key)?;
    Ok(path)
}

fn require_absolute(path: &Path, label: &str) -> Result<(), String> {
    if path.is_absolute() {
        return Ok(());
    }
    Err(format!(
        "{label} must be an absolute path: {}",
        path.display()
    ))
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::{GatewayPaths, IniConfig};

    #[test]
    fn parser_preserves_dbus_paths_and_inherits_defaults_explicitly() -> Result<(), String> {
        let config = IniConfig::parse(
            "[DEFAULT]\nAutoBatterySocPath=/Dc/Battery/Soc\nEnabled=yes\n\n[Device meter]\nPath=/Ac/L1/Power\n",
        )?;
        assert_eq!(config.get("autobatterysocpath"), Some("/Dc/Battery/Soc"));
        assert!(config.bool("Enabled", false));
        assert_eq!(config.text("Missing", "fallback"), "fallback");
        assert_eq!(
            config
                .section("device METER")
                .and_then(|values| values.get("Path")),
            Some(&"/Ac/L1/Power".to_owned()),
        );
        Ok(())
    }

    #[test]
    fn explicit_empty_values_do_not_silently_restore_device_defaults() -> Result<(), String> {
        let config = IniConfig::parse("[DEFAULT]\nAutoGridL2Path=\n")?;
        assert_eq!(config.text("AutoGridL2Path", "/Ac/Grid/L2/Power"), "");
        Ok(())
    }

    #[test]
    fn gateway_paths_match_python_defaults_and_override_contract() -> Result<(), String> {
        let config = IniConfig::parse("[DEFAULT]\nDbusGatewayRunDir=/run/custom\n")?;
        let paths = GatewayPaths::from_config(&config, None)?;
        assert_eq!(paths.socket_path, Path::new("/run/custom/gateway.sock"));
        assert_eq!(
            paths.energy_inputs_path,
            Path::new("/run/custom/energy-inputs.v4.bin")
        );

        let overridden = GatewayPaths::from_config(&config, Some(Path::new("/tmp/gateway")))?;
        assert_eq!(overridden.run_dir, Path::new("/tmp/gateway"));
        assert_eq!(
            overridden.command_dir,
            Path::new("/tmp/gateway/dbus-commands")
        );
        Ok(())
    }

    #[test]
    fn relative_runtime_paths_are_rejected() -> Result<(), String> {
        let config = IniConfig::parse("[DEFAULT]\nDbusGatewayRunDir=relative\n")?;
        assert!(GatewayPaths::from_config(&config, None).is_err());
        Ok(())
    }
}
