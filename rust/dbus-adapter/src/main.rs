// SPDX-License-Identifier: GPL-3.0-or-later

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use signal_hook::consts::signal::{SIGINT, SIGTERM};
use venus_evcharger_dbus_adapter::{run_adapter, validate_adapter_config};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("native DBus adapter failed: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let arguments = arguments()?;
    if let Arguments::Validate { config_path } = arguments {
        return validate_adapter_config(&config_path);
    }
    let Arguments::Run {
        config_path,
        run_dir,
    } = arguments
    else {
        return Err("invalid adapter launch mode".to_owned());
    };
    let stop = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(SIGTERM, stop.clone()).map_err(|error| error.to_string())?;
    signal_hook::flag::register(SIGINT, stop.clone()).map_err(|error| error.to_string())?;
    run_adapter(&config_path, run_dir.as_deref(), &stop)
}

enum Arguments {
    Run {
        config_path: PathBuf,
        run_dir: Option<PathBuf>,
    },
    Validate {
        config_path: PathBuf,
    },
}

fn arguments() -> Result<Arguments, String> {
    let mut arguments = env::args_os().skip(1);
    let first = arguments.next().map(PathBuf::from).ok_or_else(|| {
        "usage: venus-evcharger-dbus-adapter CONFIG [--run-dir DIRECTORY]".to_owned()
    })?;
    if first == PathBuf::from("--validate-launch") {
        let config_path = arguments
            .next()
            .map(PathBuf::from)
            .ok_or_else(|| "--validate-launch requires a configuration path".to_owned())?;
        if let Some(argument) = arguments.next() {
            return Err(format!(
                "unexpected validation argument: {}",
                argument.to_string_lossy()
            ));
        }
        return Ok(Arguments::Validate { config_path });
    }
    let config_path = first;
    let mut run_dir = None;
    while let Some(argument) = arguments.next() {
        if argument != "--run-dir" {
            return Err(format!("unknown argument: {}", argument.to_string_lossy()));
        }
        let value = arguments
            .next()
            .map(PathBuf::from)
            .ok_or_else(|| "--run-dir requires a directory".to_owned())?;
        run_dir = Some(value);
    }
    Ok(Arguments::Run {
        config_path,
        run_dir,
    })
}
