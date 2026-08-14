//! Command-line entrypoint for the forensic observer.

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use venus_evcharger_forensic_observer::runtime::{ObserverOptions, run, validate_config};

fn main() -> ExitCode {
    match arguments().and_then(execute) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("venus-evcharger-forensic-observer: {error}");
            ExitCode::FAILURE
        }
    }
}

#[derive(Debug, PartialEq)]
enum Action {
    Run(ObserverOptions),
    Validate(PathBuf),
}

fn execute(action: Action) -> venus_evcharger_forensic_observer::Result<()> {
    match action {
        Action::Run(options) => run(&options),
        Action::Validate(path) => validate_config(&path),
    }
}

fn arguments() -> venus_evcharger_forensic_observer::Result<Action> {
    parse_arguments(env::args().skip(1), &|name| env::var(name))
}

fn parse_arguments<I, F>(
    arguments: I,
    environment: &F,
) -> venus_evcharger_forensic_observer::Result<Action>
where
    I: IntoIterator<Item = String>,
    F: Fn(&str) -> std::result::Result<String, env::VarError>,
{
    use venus_evcharger_forensic_observer::ObserverError;

    let mut values = arguments.into_iter();
    let config_path = values
        .next()
        .ok_or_else(|| ObserverError::Configuration(usage().to_owned()))?;
    if config_path == "--help" || config_path == "-h" {
        return Err(ObserverError::Configuration(usage().to_owned()));
    }
    if config_path == "--validate-config" {
        let path = values.next().ok_or_else(|| {
            ObserverError::Configuration("--validate-config requires a path".to_owned())
        })?;
        if values.next().is_some() {
            return Err(ObserverError::Configuration(usage().to_owned()));
        }
        return Ok(Action::Validate(PathBuf::from(path)));
    }

    let mut options = ObserverOptions {
        config_path: PathBuf::from(config_path),
        start_delay_seconds: environment_number(
            environment,
            "VENUS_EVCHARGER_OBSERVER_START_DELAY",
            180.0,
        )?,
        interval_seconds: environment_number(
            environment,
            "VENUS_EVCHARGER_OBSERVER_INTERVAL",
            30.0,
        )?,
        cooldown_seconds: environment_number(
            environment,
            "VENUS_EVCHARGER_OBSERVER_COOLDOWN",
            900.0,
        )?,
        ..ObserverOptions::default()
    };
    while let Some(flag) = values.next() {
        let value = values.next().ok_or_else(|| {
            ObserverError::Configuration(format!("{flag} requires a numeric value"))
        })?;
        let parsed = finite_number(&value, &flag)?;
        match flag.as_str() {
            "--start-delay" => options.start_delay_seconds = parsed,
            "--interval" => options.interval_seconds = parsed,
            "--cooldown" => options.cooldown_seconds = parsed,
            _ => {
                return Err(ObserverError::Configuration(format!(
                    "unknown option: {flag}"
                )));
            }
        }
    }
    Ok(Action::Run(options))
}

fn environment_number<F>(
    environment: &F,
    name: &str,
    fallback: f64,
) -> venus_evcharger_forensic_observer::Result<f64>
where
    F: Fn(&str) -> std::result::Result<String, env::VarError>,
{
    match environment(name) {
        Ok(value) => finite_number(&value, name),
        Err(env::VarError::NotPresent) => Ok(fallback),
        Err(error) => Err(
            venus_evcharger_forensic_observer::ObserverError::Configuration(format!(
                "{name} is not valid text: {error}"
            )),
        ),
    }
}

fn finite_number(value: &str, name: &str) -> venus_evcharger_forensic_observer::Result<f64> {
    let number = value.parse::<f64>().map_err(|error| {
        venus_evcharger_forensic_observer::ObserverError::Configuration(format!(
            "{name} must be numeric: {error}"
        ))
    })?;
    if !number.is_finite() {
        return Err(
            venus_evcharger_forensic_observer::ObserverError::Configuration(format!(
                "{name} must be finite"
            )),
        );
    }
    Ok(number)
}

const fn usage() -> &'static str {
    "usage: venus-evcharger-forensic-observer <config-path> [--start-delay SECONDS] [--interval SECONDS] [--cooldown SECONDS]\n       venus-evcharger-forensic-observer --validate-config <config-path>"
}

#[cfg(test)]
mod tests {
    use super::{Action, parse_arguments};
    use std::collections::HashMap;
    use std::path::PathBuf;
    use venus_evcharger_forensic_observer::runtime::ObserverOptions;

    fn environment(
        values: HashMap<&str, &str>,
    ) -> impl Fn(&str) -> Result<String, std::env::VarError> {
        move |key| {
            values
                .get(key)
                .map(|value| (*value).to_owned())
                .ok_or(std::env::VarError::NotPresent)
        }
    }

    #[test]
    fn cli_defaults_and_overrides_match_the_python_contract() {
        let action = parse_arguments(["config.ini".to_owned()], &environment(HashMap::new()));
        assert_eq!(
            action,
            Ok(Action::Run(ObserverOptions {
                config_path: PathBuf::from("config.ini"),
                ..ObserverOptions::default()
            }))
        );

        let action = parse_arguments(
            [
                "config.ini".to_owned(),
                "--start-delay".to_owned(),
                "1".to_owned(),
                "--interval".to_owned(),
                "2".to_owned(),
                "--cooldown".to_owned(),
                "3".to_owned(),
            ],
            &environment(HashMap::new()),
        );
        assert_eq!(
            action,
            Ok(Action::Run(ObserverOptions {
                config_path: PathBuf::from("config.ini"),
                start_delay_seconds: 1.0,
                interval_seconds: 2.0,
                cooldown_seconds: 3.0,
                ..ObserverOptions::default()
            }))
        );
    }

    #[test]
    fn cli_rejects_non_finite_and_incomplete_values() {
        for arguments in [
            vec!["config.ini".to_owned(), "--interval".to_owned()],
            vec![
                "config.ini".to_owned(),
                "--interval".to_owned(),
                "NaN".to_owned(),
            ],
            vec![
                "config.ini".to_owned(),
                "--unknown".to_owned(),
                "1".to_owned(),
            ],
        ] {
            assert!(parse_arguments(arguments, &environment(HashMap::new())).is_err());
        }
    }
}
