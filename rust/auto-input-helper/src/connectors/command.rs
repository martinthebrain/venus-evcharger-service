//! Bounded local command returning one normalized JSON object.

use std::io::Read;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::connectors::EnergyConnector;
use crate::connectors::common::{
    finite, load_connector_document, optional_bool, optional_number, optional_string, optional_text,
};
use crate::energy::{EnergySourceDefinition, EnergySourceSnapshot};
use crate::error::{HelperError, Result};

const STDOUT_LIMIT: usize = 262_144;
const STDERR_LIMIT: usize = 16_384;

#[derive(Clone, Debug)]
struct ResponsePaths {
    soc: Option<String>,
    usable_capacity_wh: Option<String>,
    battery_power: Option<String>,
    ac_power: Option<String>,
    pv_input_power: Option<String>,
    grid_interaction: Option<String>,
    operating_mode: Option<String>,
    online: Option<String>,
    confidence: Option<String>,
}

impl ResponsePaths {
    fn load(document: &crate::ini::IniDocument) -> Self {
        Self {
            soc: optional_text(document.get("Response", "SocPath")),
            usable_capacity_wh: optional_text(document.get("Response", "UsableCapacityWhPath")),
            battery_power: optional_text(document.get("Response", "BatteryPowerPath")),
            ac_power: optional_text(document.get("Response", "AcPowerPath")),
            pv_input_power: optional_text(document.get("Response", "PvInputPowerPath")),
            grid_interaction: optional_text(document.get("Response", "GridInteractionPath")),
            operating_mode: optional_text(document.get("Response", "OperatingModePath")),
            online: optional_text(document.get("Response", "OnlinePath")),
            confidence: optional_text(document.get("Response", "ConfidencePath")),
        }
    }

    fn readable(&self) -> bool {
        [
            &self.soc,
            &self.usable_capacity_wh,
            &self.battery_power,
            &self.ac_power,
            &self.pv_input_power,
            &self.grid_interaction,
        ]
        .into_iter()
        .any(Option::is_some)
    }
}

/// Direct-exec command connector with output and wall-clock bounds.
pub struct CommandJsonConnector {
    command: Vec<String>,
    timeout_seconds: f64,
    paths: ResponsePaths,
}

impl CommandJsonConnector {
    pub fn load(source: &EnergySourceDefinition, default_timeout: f64) -> Result<Self> {
        let document = load_connector_document(&source.config_path)?;
        let command = shell_words(document.get("Command", "Args").unwrap_or(""))?;
        if command.is_empty() {
            return Err(HelperError::Configuration(format!(
                "energy source {:?} requires Command.Args",
                source.source_id
            )));
        }
        let configured = finite(document.get("Command", "TimeoutSeconds"))
            .or_else(|| finite(document.get("Adapter", "RequestTimeoutSeconds")));
        let timeout_seconds = configured
            .filter(|value| *value > 0.0)
            .unwrap_or(default_timeout);
        let paths = ResponsePaths::load(&document);
        if !paths.readable() && source.usable_capacity_wh.is_none() {
            return Err(HelperError::Configuration(format!(
                "energy source {:?} requires a response path or usable capacity",
                source.source_id
            )));
        }
        Ok(Self {
            command,
            timeout_seconds,
            paths,
        })
    }
}

impl EnergyConnector for CommandJsonConnector {
    fn read_step(
        &mut self,
        source: &EnergySourceDefinition,
        observed_at: f64,
        timeout_seconds: f64,
    ) -> Result<Option<EnergySourceSnapshot>> {
        let stdout = run_bounded_command(&self.command, self.timeout_seconds.min(timeout_seconds))?;
        let Value::Object(payload) = serde_json::from_slice::<Value>(&stdout)? else {
            return Err(HelperError::Input(format!(
                "energy source {:?} command did not return a JSON object",
                source.source_id
            )));
        };
        let soc = optional_number(&payload, self.paths.soc.as_deref())?
            .filter(|value| (0.0..=100.0).contains(value));
        let capacity = optional_number(&payload, self.paths.usable_capacity_wh.as_deref())?;
        let usable_capacity_wh = match capacity {
            Some(value) if value > 0.0 => Some(value),
            Some(_) => None,
            None => source.usable_capacity_wh,
        };
        Ok(Some(EnergySourceSnapshot {
            source_id: source.source_id.clone(),
            role: source.role,
            service_name: if source.service_name.is_empty() {
                self.command
                    .first()
                    .cloned()
                    .unwrap_or_else(|| source.source_id.clone())
            } else {
                source.service_name.clone()
            },
            soc,
            usable_capacity_wh,
            usable_capacity_source: String::new(),
            installed_capacity_ah: None,
            capacity_voltage_v: None,
            capacity_nominal_voltage_v: None,
            capacity_cell_count: None,
            battery_chemistry: source.battery_chemistry.clone(),
            net_battery_power_w: optional_number(&payload, self.paths.battery_power.as_deref())?,
            charge_limit_power_w: None,
            discharge_limit_power_w: None,
            ac_power_w: optional_number(&payload, self.paths.ac_power.as_deref())?,
            pv_input_power_w: optional_number(&payload, self.paths.pv_input_power.as_deref())?,
            grid_interaction_w: optional_number(&payload, self.paths.grid_interaction.as_deref())?,
            ac_power_scope_key: String::new(),
            pv_input_power_scope_key: String::new(),
            grid_interaction_scope_key: String::new(),
            operating_mode: optional_string(&payload, self.paths.operating_mode.as_deref())?
                .unwrap_or_default(),
            online: optional_bool(&payload, self.paths.online.as_deref())?.unwrap_or(true),
            confidence: optional_number(&payload, self.paths.confidence.as_deref())?
                .map_or(1.0, |value| value.clamp(0.0, 1.0)),
            captured_at: Some(observed_at),
            physical_id: source.physical_id.clone(),
            physical_priority: source.physical_priority,
        }))
    }
}

fn run_bounded_command(command: &[String], timeout_seconds: f64) -> Result<Vec<u8>> {
    let executable = command.first().ok_or_else(|| {
        HelperError::Configuration("command connector has no executable".to_owned())
    })?;
    let mut child = Command::new(executable)
        .args(&command[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| HelperError::input("start energy helper command", &error))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| HelperError::Runtime("command stdout pipe is unavailable".to_owned()))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| HelperError::Runtime("command stderr pipe is unavailable".to_owned()))?;
    let stdout_reader = thread::spawn(move || read_limited(stdout, STDOUT_LIMIT));
    let stderr_reader = thread::spawn(move || read_limited(stderr, STDERR_LIMIT));
    let deadline = Instant::now() + Duration::from_secs_f64(timeout_seconds.max(0.001));
    let status = loop {
        match child
            .try_wait()
            .map_err(|error| HelperError::input("wait for energy helper command", &error))?
        {
            Some(status) => break status,
            None if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            None => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(HelperError::Input(
                    "energy helper command exceeded its deadline".to_owned(),
                ));
            }
        }
    };
    let (stdout, stdout_overflow) = join_reader(stdout_reader, "stdout")?;
    let (_, stderr_overflow) = join_reader(stderr_reader, "stderr")?;
    if stdout_overflow || stderr_overflow {
        return Err(HelperError::Input(
            "energy helper command output exceeded its limit".to_owned(),
        ));
    }
    if !status.success() {
        return Err(HelperError::Input(format!(
            "energy helper command exited with status {}",
            status
                .code()
                .map_or_else(|| "signal".to_owned(), |code| code.to_string())
        )));
    }
    Ok(stdout)
}

fn read_limited(mut reader: impl Read, limit: usize) -> (Vec<u8>, bool) {
    let mut output = Vec::new();
    let mut buffer = [0_u8; 4096];
    let mut overflow = false;
    loop {
        match reader.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => {
                let remaining = limit.saturating_sub(output.len());
                output.extend_from_slice(&buffer[..count.min(remaining)]);
                overflow |= count > remaining;
            }
        }
    }
    (output, overflow)
}

fn join_reader(
    handle: thread::JoinHandle<(Vec<u8>, bool)>,
    label: &str,
) -> Result<(Vec<u8>, bool)> {
    handle
        .join()
        .map_err(|_| HelperError::Runtime(format!("command {label} reader failed")))
}

fn shell_words(raw: &str) -> Result<Vec<String>> {
    let mut result = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut escaped = false;
    let mut token_started = false;
    for character in raw.chars() {
        if escaped {
            current.push(character);
            escaped = false;
            token_started = true;
            continue;
        }
        match (quote, character) {
            (Some('\''), '\'') | (Some('"'), '"') => quote = None,
            (Some('"') | None, '\\') => {
                escaped = true;
                token_started = true;
            }
            (None, '\'' | '"') => {
                quote = Some(character);
                token_started = true;
            }
            (None, value) if value.is_whitespace() => {
                if token_started {
                    result.push(std::mem::take(&mut current));
                    token_started = false;
                }
            }
            (_, value) => {
                current.push(value);
                token_started = true;
            }
        }
    }
    if escaped || quote.is_some() {
        return Err(HelperError::Configuration(
            "Command.Args contains an incomplete quote or escape".to_owned(),
        ));
    }
    if token_started {
        result.push(current);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::{CommandJsonConnector, shell_words};
    use crate::connectors::EnergyConnector;
    use crate::energy::{ConnectorType, EnergyRole, EnergySourceDefinition};

    fn source(config_path: String) -> EnergySourceDefinition {
        EnergySourceDefinition {
            source_id: "command-source".to_owned(),
            profile_name: "command-json".to_owned(),
            role: EnergyRole::HybridInverter,
            connector_type: Some(ConnectorType::CommandJson),
            config_path,
            service_name: String::new(),
            usable_capacity_wh: Some(5_000.0),
            battery_chemistry: "lfp".to_owned(),
            capacity_auto_estimate: false,
            capacity_estimate_min_soc: 95.0,
            capacity_startup_recheck_seconds: 300.0,
            estimated_capacity_wh: None,
            estimated_capacity_ah: None,
            estimated_capacity_nominal_voltage_v: None,
            estimated_capacity_cell_count: None,
            physical_id: "bank-a".to_owned(),
            physical_priority: 4,
        }
    }

    #[test]
    fn command_arguments_are_split_without_a_shell() {
        assert_eq!(
            shell_words("/bin/tool --name 'two words' \"three words\" '' \"\""),
            Ok(vec![
                "/bin/tool".to_owned(),
                "--name".to_owned(),
                "two words".to_owned(),
                "three words".to_owned(),
                String::new(),
                String::new(),
            ])
        );
        assert!(shell_words("'unfinished").is_err());
    }

    #[test]
    fn command_connector_executes_without_a_shell_and_maps_the_full_snapshot()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempdir()?;
        let config_path = directory.path().join("connector.ini");
        fs::write(
            &config_path,
            "[Command]\nArgs=/usr/bin/printf '{\"battery\":{\"soc\":80.5,\"capacity\":10000,\"power\":-1200},\"solar\":3200,\"grid\":-450,\"mode\":\"hybrid\",\"online\":1,\"confidence\":0.75}'\nTimeoutSeconds=1\n[Response]\nSocPath=battery.soc\nUsableCapacityWhPath=battery.capacity\nBatteryPowerPath=battery.power\nPvInputPowerPath=solar\nGridInteractionPath=grid\nOperatingModePath=mode\nOnlinePath=online\nConfidencePath=confidence\n",
        )?;
        let definition = source(config_path.to_string_lossy().into_owned());
        let mut connector = CommandJsonConnector::load(&definition, 2.0)?;
        let snapshot = connector
            .read_step(&definition, 100.0, 2.0)?
            .ok_or("command connector did not complete")?;

        assert_eq!(snapshot.service_name, "/usr/bin/printf");
        assert_eq!(snapshot.soc, Some(80.5));
        assert_eq!(snapshot.usable_capacity_wh, Some(10_000.0));
        assert_eq!(snapshot.net_battery_power_w, Some(-1_200.0));
        assert_eq!(snapshot.pv_input_power_w, Some(3_200.0));
        assert_eq!(snapshot.grid_interaction_w, Some(-450.0));
        assert_eq!(snapshot.operating_mode, "hybrid");
        assert!(snapshot.online);
        assert!((snapshot.confidence - 0.75).abs() < f64::EPSILON);
        assert_eq!(snapshot.captured_at, Some(100.0));
        Ok(())
    }
}
