// SPDX-License-Identifier: GPL-3.0-or-later
//! Process-level contract for the native gateway's D-Bus and IPC boundaries.

use std::fs;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tempfile::tempdir;
use zbus::blocking::connection::Builder;
use zbus::blocking::{Connection, Proxy};
use zbus::interface;
use zbus::zvariant::OwnedValue;

const EVCS_SERVICE: &str = "com.victronenergy.evcharger.http_60";
const BUS_ITEM_INTERFACE: &str = "com.victronenergy.BusItem";
const WAIT_LIMIT: Duration = Duration::from_secs(8);

#[derive(Clone, Copy)]
struct TestBusItem {
    value: f64,
}

#[interface(name = "com.victronenergy.BusItem", spawn = false)]
impl TestBusItem {
    #[allow(clippy::trivially_copy_pass_by_ref)]
    fn get_value(&self) -> OwnedValue {
        OwnedValue::from(self.value)
    }
}

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ignored = self.0.kill();
        let _ignored = self.0.wait();
    }
}

#[test]
fn native_process_preserves_semantic_publication_and_gui_control() -> Result<(), String> {
    if Command::new("dbus-daemon")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_err()
    {
        return Ok(());
    }

    let (address, daemon) = start_private_bus()?;
    let _daemon = ChildGuard(daemon);
    let _sources = source_service(&address)?;
    let client = Builder::address(address.as_str())
        .map_err(|error| error.to_string())?
        .build()
        .map_err(|error| error.to_string())?;

    let temporary = tempdir().map_err(|error| error.to_string())?;
    let config_path = temporary.path().join("config.ini");
    let run_dir = temporary.path().join("run");
    fs::write(&config_path, test_config()).map_err(|error| error.to_string())?;

    let binary = env!("CARGO_BIN_EXE_venus-evcharger-dbus-adapter");
    let adapter = Command::new(binary)
        .arg(&config_path)
        .arg("--run-dir")
        .arg(&run_dir)
        .env("DBUS_SYSTEM_BUS_ADDRESS", &address)
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|error| error.to_string())?;
    let mut adapter = ChildGuard(adapter);

    wait_for_path(&run_dir.join("gateway.sock")).map_err(|error| format!("socket: {error}"))?;
    enqueue(&run_dir, "register.json", &registration())?;
    wait_for_i32(&client, "/Mode", 0).map_err(|error| format!("registration: {error}"))?;

    enqueue(
        &run_dir,
        "publish.json",
        &json!({
            "kind": "publish_evcs_fields",
            "fields": {"mode": 2},
            "publication_priority": "critical",
            "priority": "safety"
        }),
    )?;
    wait_for_i32(&client, "/Mode", 2).map_err(|error| format!("publication: {error}"))?;

    let proxy = Proxy::new(&client, EVCS_SERVICE, "/Mode", BUS_ITEM_INTERFACE)
        .map_err(|error| error.to_string())?;
    let response: i32 = proxy
        .call("SetValue", &(OwnedValue::from(1_i32),))
        .map_err(|error| error.to_string())?;
    assert_eq!(response, 2, "GUI writes remain asynchronous");
    let command = wait_for_core_command(&run_dir)?;
    assert_eq!(command["kind"], "user_command");
    assert_eq!(command["name"], "set_mode");
    assert_eq!(command["target"], "mode");
    assert_eq!(command["value"], 1);

    wait_for_path(&run_dir.join("energy-inputs.v4.bin"))?;
    let energy =
        fs::read(run_dir.join("energy-inputs.v4.bin")).map_err(|error| error.to_string())?;
    assert_eq!(energy.get(..5), Some(b"VEI4\x04".as_slice()));
    wait_for_path(&run_dir.join("dbus-health.json"))?;
    thread::sleep(Duration::from_secs(1));
    assert!(
        adapter
            .0
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none(),
        "native adapter must remain alive after real D-Bus measurements are published"
    );

    adapter.0.kill().map_err(|error| error.to_string())?;
    let status = adapter.0.wait().map_err(|error| error.to_string())?;
    assert!(
        !status.success(),
        "the test terminates the long-running adapter"
    );
    Ok(())
}

fn start_private_bus() -> Result<(String, Child), String> {
    let mut child = Command::new("dbus-daemon")
        .args(["--session", "--print-address=1", "--nofork"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| error.to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "private D-Bus did not expose its address".to_owned())?;
    let mut address = String::new();
    BufReader::new(stdout)
        .read_line(&mut address)
        .map_err(|error| error.to_string())?;
    let address = address.trim().to_owned();
    if address.is_empty() {
        return Err("private D-Bus returned an empty address".to_owned());
    }
    Ok((address, child))
}

fn source_service(address: &str) -> Result<Connection, String> {
    let builder = Builder::address(address)
        .map_err(|error| error.to_string())?
        .name("com.victronenergy.system")
        .map_err(|error| error.to_string())?;
    let paths = [
        ("/Ac/Grid/L1/Power", 100.0),
        ("/Ac/Grid/L2/Power", 200.0),
        ("/Ac/Grid/L3/Power", 300.0),
        ("/Dc/Pv/Power", 1200.0),
        ("/Dc/Battery/Soc", 55.0),
        ("/Dc/Battery/Power", -400.0),
        ("/InstalledCapacity", 200.0),
        ("/Dc/0/Voltage", 51.2),
    ];
    let mut builder = builder;
    for (path, value) in paths {
        builder = builder
            .serve_at(path, TestBusItem { value })
            .map_err(|error| error.to_string())?;
    }
    builder.build().map_err(|error| error.to_string())
}

const fn test_config() -> &'static str {
    "[DEFAULT]\n\
DeviceInstance=60\n\
ServiceName=com.victronenergy.evcharger\n\
AutoGridService=com.victronenergy.system\n\
AutoPvService=com.victronenergy.system\n\
AutoPvPath=/Dc/Pv/Power\n\
AutoUseDcPv=false\n\
AutoBatteryService=com.victronenergy.system\n\
AutoBatteryPowerService=com.victronenergy.system\n\
DbusGatewayGridReadIntervalSeconds=0.2\n\
DbusGatewayPvReadIntervalSeconds=0.2\n\
DbusGatewayBatterySocReadIntervalSeconds=0.2\n\
DbusGatewayBatteryPowerReadIntervalSeconds=0.2\n\
DbusGatewayEnergyPublishIntervalSeconds=0.2\n\
DbusGatewayHealthPublishIntervalSeconds=0.2\n"
}

fn registration() -> Value {
    json!({
        "kind": "register_evcs",
        "identity": {
            "product_name": "Native EVCS",
            "custom_name": "Native EVCS",
            "firmware_version": "test",
            "hardware_version": "simulated",
            "serial": "native-test-60",
            "connection_name": "Private test bus",
            "process_name": "native_runtime",
            "process_version": "Rust"
        },
        "fields": {"connected": 1, "mode": 0}
    })
}

fn enqueue(run_dir: &Path, name: &str, payload: &Value) -> Result<(), String> {
    let directory = run_dir.join("dbus-commands");
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    fs::write(
        directory.join(name),
        serde_json::to_vec(&payload).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())
}

fn wait_for_i32(connection: &Connection, path: &str, expected: i32) -> Result<(), String> {
    wait_until(|| {
        let proxy = Proxy::new(connection, EVCS_SERVICE, path, BUS_ITEM_INTERFACE).ok()?;
        let value: OwnedValue = proxy.call("GetValue", &()).ok()?;
        (value.downcast_ref::<i32>().ok()? == expected).then_some(())
    })
}

fn wait_for_path(path: &Path) -> Result<(), String> {
    wait_until(|| path.exists().then_some(()))
}

fn wait_for_core_command(run_dir: &Path) -> Result<Value, String> {
    let directory = run_dir.join("core-commands");
    let mut found = None;
    wait_until(|| {
        let entries = fs::read_dir(&directory).ok()?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path
                .extension()
                .is_some_and(|extension| extension == "json")
            {
                found = fs::read(&path)
                    .ok()
                    .and_then(|payload| serde_json::from_slice(&payload).ok());
                if found.is_some() {
                    return Some(());
                }
            }
        }
        None
    })?;
    found.ok_or_else(|| "GUI write did not reach the core mailbox".to_owned())
}

fn wait_until(mut predicate: impl FnMut() -> Option<()>) -> Result<(), String> {
    let deadline = Instant::now() + WAIT_LIMIT;
    while Instant::now() < deadline {
        if predicate().is_some() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(25));
    }
    Err("timed out waiting for native adapter contract".to_owned())
}
