//! Command-line entrypoint for the native Auto input helper.

use std::env;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use venus_evcharger_auto_input_helper::config::HelperConfig;
use venus_evcharger_auto_input_helper::connectors::build_connector;
use venus_evcharger_auto_input_helper::error::{HelperError, Result};
use venus_evcharger_auto_input_helper::runtime::{RuntimeIdentity, run, run_once};

fn main() -> ExitCode {
    match arguments().and_then(execute) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("venus-evcharger-auto-input-helper: {error}");
            ExitCode::FAILURE
        }
    }
}

#[derive(Debug, Eq, PartialEq)]
enum Action {
    Run(LaunchArguments),
    Once(LaunchArguments),
    ValidateConfig(PathBuf),
    ValidateLaunch(PathBuf),
    Help,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct LaunchArguments {
    config_path: PathBuf,
    snapshot_path: Option<PathBuf>,
    parent_pid: Option<u32>,
    helper_generation: u64,
    runtime_instance_id: String,
}

fn execute(action: Action) -> Result<()> {
    match action {
        Action::Run(arguments) => execute_launch(arguments, false),
        Action::Once(arguments) => execute_launch(arguments, true),
        Action::ValidateConfig(path) => validate_native(&path),
        Action::ValidateLaunch(path) => validate_launch(&path),
        Action::Help => {
            println!("{}", usage());
            Ok(())
        }
    }
}

fn execute_launch(arguments: LaunchArguments, once: bool) -> Result<()> {
    let config = HelperConfig::load(&arguments.config_path, arguments.snapshot_path.as_deref())?;
    let identity = RuntimeIdentity::new(
        arguments.parent_pid,
        arguments.helper_generation,
        arguments.runtime_instance_id,
    );
    if once {
        run_once(&config, identity)
    } else {
        run(&config, identity)
    }
}

fn validate_native(path: &Path) -> Result<()> {
    HelperConfig::load(path, None)?;
    println!("native-rust");
    Ok(())
}

fn validate_launch(path: &Path) -> Result<()> {
    let config = HelperConfig::load(path, None)?;
    for source in &config.energy_sources {
        build_connector(source, config.energy_source_request_timeout_seconds)?;
    }
    println!("native-rust");
    Ok(())
}

fn arguments() -> Result<Action> {
    parse_arguments(env::args().skip(1))
}

fn parse_arguments<I>(arguments: I) -> Result<Action>
where
    I: IntoIterator<Item = String>,
{
    let values: Vec<String> = arguments.into_iter().collect();
    let Some(first) = values.first() else {
        return Ok(Action::Run(LaunchArguments {
            config_path: default_config_path()?,
            snapshot_path: None,
            parent_pid: None,
            helper_generation: 0,
            runtime_instance_id: generated_runtime_id()?,
        }));
    };
    match first.as_str() {
        "--help" | "-h" => {
            require_length(&values, 1)?;
            Ok(Action::Help)
        }
        "--validate-config" => {
            require_length(&values, 2)?;
            Ok(Action::ValidateConfig(PathBuf::from(&values[1])))
        }
        "--validate-launch" => {
            require_length(&values, 2)?;
            Ok(Action::ValidateLaunch(PathBuf::from(&values[1])))
        }
        "--once" => parse_launch(&values[1..]).map(Action::Once),
        _ => parse_launch(&values).map(Action::Run),
    }
}

fn parse_launch(values: &[String]) -> Result<LaunchArguments> {
    if values.is_empty() || values.len() > 5 {
        return Err(HelperError::Configuration(usage().to_owned()));
    }
    let snapshot_path = values
        .get(1)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let parent_pid = optional_integer(values.get(2), "parent_pid")?
        .map(u32::try_from)
        .transpose()
        .map_err(|error| {
            HelperError::Configuration(format!(
                "parent_pid is outside the supported range: {error}"
            ))
        })?;
    let helper_generation = optional_integer(values.get(3), "helper_generation")?.unwrap_or(0);
    let runtime_instance_id = values
        .get(4)
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .map_or_else(generated_runtime_id, |value| Ok(value.to_owned()))?;
    Ok(LaunchArguments {
        config_path: PathBuf::from(&values[0]),
        snapshot_path,
        parent_pid,
        helper_generation,
        runtime_instance_id,
    })
}

fn optional_integer(value: Option<&String>, label: &str) -> Result<Option<u64>> {
    let Some(raw) = value
        .map(|item| item.trim())
        .filter(|item| !item.is_empty())
    else {
        return Ok(None);
    };
    raw.parse::<u64>()
        .map(Some)
        .map_err(|error| HelperError::Configuration(format!("{label} is invalid: {error}")))
}

fn require_length(values: &[String], expected: usize) -> Result<()> {
    if values.len() == expected {
        return Ok(());
    }
    Err(HelperError::Configuration(usage().to_owned()))
}

fn default_config_path() -> Result<PathBuf> {
    let executable = env::current_exe()
        .map_err(|error| HelperError::Runtime(format!("cannot locate executable: {error}")))?;
    let venus_directory = executable
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| HelperError::Runtime("cannot locate Venus configuration".to_owned()))?;
    Ok(venus_directory.join("config.venus_evcharger.ini"))
}

fn generated_runtime_id() -> Result<String> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| HelperError::Runtime(format!("system clock precedes epoch: {error}")))?
        .as_nanos();
    Ok(format!("rust-{}-{nanos}", std::process::id()))
}

const fn usage() -> &'static str {
    "usage: venus-evcharger-auto-input-helper [CONFIG [SNAPSHOT [PARENT_PID [GENERATION [RUNTIME_ID]]]]]\n       venus-evcharger-auto-input-helper --once CONFIG [SNAPSHOT [PARENT_PID [GENERATION [RUNTIME_ID]]]]\n       venus-evcharger-auto-input-helper --validate-config CONFIG\n       venus-evcharger-auto-input-helper --validate-launch CONFIG"
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;

    use tempfile::tempdir;

    use super::{Action, LaunchArguments, parse_arguments, validate_launch, validate_native};

    #[test]
    fn positional_contract_matches_the_python_supervisor_launch() {
        let action = parse_arguments([
            "/repo/deploy/venus/config.ini".to_owned(),
            "/run/auto.json".to_owned(),
            "42".to_owned(),
            "7".to_owned(),
            "runtime-a".to_owned(),
        ]);
        assert_eq!(
            action,
            Ok(Action::Run(LaunchArguments {
                config_path: PathBuf::from("/repo/deploy/venus/config.ini"),
                snapshot_path: Some(PathBuf::from("/run/auto.json")),
                parent_pid: Some(42),
                helper_generation: 7,
                runtime_instance_id: "runtime-a".to_owned(),
            }))
        );
    }

    #[test]
    fn validation_modes_are_unambiguous() {
        assert_eq!(
            parse_arguments(["--validate-launch".to_owned(), "/tmp/config.ini".to_owned(),]),
            Ok(Action::ValidateLaunch(PathBuf::from("/tmp/config.ini")))
        );
        assert!(parse_arguments(["--validate-launch".to_owned()]).is_err());
    }

    #[test]
    fn launch_validation_checks_connector_files_without_performing_io()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempdir()?;
        let config_path = directory.path().join("config.ini");
        let missing_connector = directory.path().join("missing.ini");
        fs::write(
            &config_path,
            format!(
                "[DEFAULT]\nAutoInputSnapshotPath=/run/auto.json\nAutoEnergySources=external\nAutoEnergySource.external.Profile=template-http-hybrid\nAutoEnergySource.external.ConfigPath={}\n",
                missing_connector.display()
            ),
        )?;

        assert!(validate_native(&config_path).is_ok());
        assert!(validate_launch(&config_path).is_err());

        let connector_path = directory.path().join("external.ini");
        fs::write(
            &connector_path,
            "[Adapter]\nBaseUrl=http://127.0.0.1:9\n[EnergyRequest]\nUrl=/energy\n[EnergyResponse]\nPvInputPowerPath=power\n",
        )?;
        fs::write(
            &config_path,
            format!(
                "[DEFAULT]\nAutoInputSnapshotPath=/run/auto.json\nAutoEnergySources=external\nAutoEnergySource.external.Profile=template-http-hybrid\nAutoEnergySource.external.ConfigPath={}\n",
                connector_path.display()
            ),
        )?;
        assert!(validate_launch(&config_path).is_ok());
        Ok(())
    }
}
