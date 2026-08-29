#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run focused process-level Python/Rust auto-input parity scenarios."""

from __future__ import annotations

import argparse
import copy
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.dev.compare_auto_input_helper_snapshots import (
    SnapshotParityError,
    compare_snapshots,
    load_snapshot,
)
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, MeasuredValue
from venus_evcharger.ipc.energy_binary import write_energy_inputs_file

PROCESS_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    config: str = ""
    values: Mapping[str, float | None] | None = None
    age_seconds: float = 0.0
    observed_epoch: float | None = None
    expected: Mapping[str, object] | None = None


class _QuietJsonHandler(BaseHTTPRequestHandler):
    payload: bytes = b"{}"
    delay_seconds: float = 0.0

    def do_GET(self) -> None:
        if self.delay_seconds > 0.0:
            time.sleep(self.delay_seconds)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        try:
            self.wfile.write(self.payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(  # pylint: disable=redefined-builtin
        self,
        format: str,
        *_args: object,
    ) -> None:
        del format
        return


class _ModbusHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = _receive_exact(self.request, 12)
        if len(request) != 12:
            return
        transaction = request[:2]
        unit_id = request[6]
        function = request[7]
        address = int.from_bytes(request[8:10], "big")
        values = {10: 805}
        value = values.get(address, 0).to_bytes(2, "big")
        pdu = bytes((function, len(value))) + value
        response = transaction + b"\x00\x00" + len(pdu + bytes((unit_id,))).to_bytes(2, "big")
        self.request.sendall(response + bytes((unit_id,)) + pdu)


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _receive_exact(connection: object, length: int) -> bytes:
    receiver = getattr(connection, "recv")
    result = bytearray()
    while len(result) < length:
        block = receiver(length - len(result))
        if not block:
            break
        result.extend(block)
    return bytes(result)


@contextmanager
def json_server(
    payload: Mapping[str, object],
    *,
    delay_seconds: float = 0.0,
) -> Generator[str, None, None]:
    handler = type(
        "ScenarioJsonHandler",
        (_QuietJsonHandler,),
        {
            "payload": json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            "delay_seconds": delay_seconds,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@contextmanager
def controlled_json_server(
    payload: Mapping[str, object],
) -> Generator[tuple[str, type[_QuietJsonHandler]], None, None]:
    handler = type(
        "ControlledScenarioJsonHandler",
        (_QuietJsonHandler,),
        {"payload": json.dumps(payload, separators=(",", ":")).encode("utf-8")},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@contextmanager
def modbus_server() -> Generator[tuple[str, int], None, None]:
    server = _ThreadingTcpServer(("127.0.0.1", 0), _ModbusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _measurement(
    value: float | None,
    epoch: float,
    monotonic: float,
    source_id: str,
) -> MeasuredValue:
    if value is None:
        return MeasuredValue(
            None,
            0.0,
            "unknown",
            0.0,
            (),
            "not-observed",
            observed_monotonic=0.0,
        )
    return MeasuredValue(
        value,
        epoch,
        "fresh",
        1.0,
        (source_id,),
        "",
        observed_monotonic=monotonic,
    )


def _write_gateway_snapshot(
    run_dir: Path,
    values: Mapping[str, float | None],
    *,
    age_seconds: float,
    observed_epoch: float | None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    captured_monotonic = time.monotonic()
    observed_monotonic = max(0.001, captured_monotonic - age_seconds)
    captured_epoch = time.time()
    observation_epoch = captured_epoch - age_seconds if observed_epoch is None else observed_epoch
    snapshot = EnergyInputsSnapshot(
        sequence=1,
        captured_at=captured_epoch,
        captured_monotonic=captured_monotonic,
        topology_generation=1,
        grid_power_w=_measurement(values.get("grid"), observation_epoch, observed_monotonic, "grid"),
        pv_power_w=_measurement(values.get("pv"), observation_epoch, observed_monotonic, "pv"),
        battery_soc=_measurement(values.get("soc"), observation_epoch, observed_monotonic, "battery"),
        battery_net_power_w=_measurement(
            values.get("battery_power"), observation_epoch, observed_monotonic, "battery"
        ),
        battery_capacity_wh=_measurement(
            values.get("capacity_wh"), observation_epoch, observed_monotonic, "battery"
        ),
        battery_capacity_ah=_measurement(
            values.get("capacity_ah"), observation_epoch, observed_monotonic, "battery"
        ),
        battery_voltage_v=_measurement(
            values.get("voltage"), observation_epoch, observed_monotonic, "battery"
        ),
    )
    write_energy_inputs_file(str(run_dir / "energy-inputs.v4.bin"), snapshot)


def _base_config(run_dir: Path) -> str:
    return f"""[DEFAULT]
DbusGatewayRunDir={run_dir}
DbusGatewayMaxAgeSeconds=5
DbusGatewayErrorRetrySeconds=1
AutoInputPollIntervalMs=200
AutoPvPollIntervalMs=200
AutoGridPollIntervalMs=200
AutoBatteryPollIntervalMs=200
AutoInputValidationPollSeconds=5
EnergyTopologyRefreshSeconds=5
AutoBatteryCapacityAutoEstimate=1
AutoBatteryCapacityEstimateMinSoc=95
AutoBatteryCapacityStartupRecheckSeconds=3600
ExternalEnergySourceRequestTimeoutSeconds=0.25
ExternalEnergySourcePollIntervalSeconds=0.2
ExternalEnergySourceBackoffBaseSeconds=0.2
ExternalEnergySourceBackoffMaxSeconds=0.2
ExternalEnergySourceLastGoodMaxAgeSeconds=1
ExternalEnergySourceCycleBudgetSeconds=0.3
"""


def _run_once(
    executable: Sequence[str],
    config_path: Path,
    snapshot_path: Path,
    runtime_id: str,
) -> dict[str, object]:
    command = [
        *executable,
        "--once",
        str(config_path),
        str(snapshot_path),
        str(os.getpid()),
        "7",
        runtime_id,
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"{' '.join(command[:2])} failed: {detail}")
    return load_snapshot(snapshot_path)


def _assert_expected(name: str, payload: Mapping[str, object], expected: Mapping[str, object]) -> None:
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(
                f"scenario {name} produced {key}={payload.get(key)!r}, expected {value!r}"
            )


def _external_source(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    sources = payload.get("battery_sources")
    if not isinstance(sources, list):
        return None
    for value in cast(list[object], sources):
        if not isinstance(value, dict):
            continue
        source = cast(dict[str, object], value)
        if source.get("source_id") == "external":
            return source
    return None


def _wait_snapshot(
    path: Path,
    predicate: Callable[[Mapping[str, object]], bool],
    deadline: float,
) -> dict[str, object]:
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            last_payload = load_snapshot(path)
        except SnapshotParityError:
            time.sleep(0.05)
            continue
        if predicate(last_payload):
            return last_payload
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for scenario snapshot {path}: {last_payload!r}")


def _start_helper(
    executable: Sequence[str],
    config_path: Path,
    snapshot_path: Path,
    runtime_id: str,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            *executable,
            str(config_path),
            str(snapshot_path),
            str(os.getpid()),
            "7",
            runtime_id,
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop_helper(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3.0)


def _recovery_source_succeeded(payload: Mapping[str, object]) -> bool:
    return (_external_source(payload) or {}).get("poll_status") == "success"


def _recovery_source_uses_last_good(payload: Mapping[str, object]) -> bool:
    source = _external_source(payload) or {}
    failures = source.get("consecutive_failures")
    return isinstance(failures, int) and failures >= 1 and source.get("contributing") is True


def _recovery_source_restored(payload: Mapping[str, object]) -> bool:
    source = _external_source(payload) or {}
    return source.get("poll_status") == "success" and source.get("consecutive_failures") == 0


def _prepare_recovery_run(execution_root: Path, base_url: str) -> tuple[Path, Path]:
    gateway_dir = execution_root / "gateway"
    execution_root.mkdir(parents=True)
    _write_gateway_snapshot(
        gateway_dir,
        {"grid": 0.0, "pv": 100.0, "soc": 60.0, "battery_power": 0.0},
        age_seconds=0.0,
        observed_epoch=None,
    )
    connector = execution_root / "source.ini"
    connector.write_text(
        f"""[Adapter]
BaseUrl={base_url}
RequestTimeoutSeconds=0.1
[EnergyRequest]
Url=/energy
[EnergyResponse]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
PvInputPowerPath=data.pv_w
OnlinePath=data.online
""",
        encoding="utf-8",
    )
    config_path = execution_root / "config.ini"
    config_path.write_text(
        _base_config(gateway_dir) + _external_config("template-http-hybrid", connector),
        encoding="utf-8",
    )
    return config_path, execution_root / "snapshot.json"


def _capture_recovery_states(
    executable: Sequence[str],
    runtime_name: str,
    config_path: Path,
    snapshot_path: Path,
    handler: type[_QuietJsonHandler],
) -> tuple[dict[str, object], dict[str, object]]:
    process = _start_helper(executable, config_path, snapshot_path, runtime_name)
    try:
        deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
        _wait_snapshot(snapshot_path, _recovery_source_succeeded, deadline)
        handler.delay_seconds = 0.5
        last_good = _wait_snapshot(snapshot_path, _recovery_source_uses_last_good, deadline)
        handler.delay_seconds = 0.0
        recovered = _wait_snapshot(snapshot_path, _recovery_source_restored, deadline)
        return copy.deepcopy(last_good), copy.deepcopy(recovered)
    finally:
        _stop_helper(process)


def _run_timeout_last_good_recovery_once(
    executable: Sequence[str],
    runtime_name: str,
    root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    execution_root = root / runtime_name
    payload = {
        "data": {
            "soc": 70.0,
            "capacity_wh": 8000.0,
            "pv_w": 2100.0,
            "online": True,
        }
    }
    with controlled_json_server(payload) as (base_url, handler):
        config_path, snapshot_path = _prepare_recovery_run(execution_root, base_url)
        return _capture_recovery_states(
            executable,
            runtime_name,
            config_path,
            snapshot_path,
            handler,
        )


def _normalize_named_error(key: str, value: object) -> object:
    if key == "last_error" and isinstance(value, str) and value:
        return "<error>"
    return _normalize_error_details(value)


def _normalize_error_details(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        return {key: _normalize_named_error(key, item) for key, item in mapping.items()}
    if isinstance(value, list):
        return [_normalize_error_details(item) for item in cast(list[object], value)]
    return value


def _assert_recovery_parity(
    label: str,
    python_payload: Mapping[str, object],
    rust_payload: Mapping[str, object],
) -> None:
    left = _normalize_error_details(python_payload)
    right = _normalize_error_details(rust_payload)
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise RuntimeError(f"scenario {label} did not produce object snapshots")
    result = compare_snapshots(
        cast(dict[str, object], left),
        cast(dict[str, object], right),
    )
    if not result.equal:
        details = "\n".join(f"  - {item}" for item in result.differences)
        raise RuntimeError(f"scenario {label} differs:\n{details}")


def _run_timeout_last_good_recovery(rust_binary: Path, root: Path) -> None:
    python_states = _run_timeout_last_good_recovery_once(
        (sys.executable, str(REPO_ROOT / "venus_evcharger_auto_input_helper.py")),
        "python-timeout-recovery",
        root,
    )
    rust_states = _run_timeout_last_good_recovery_once(
        (str(rust_binary),),
        "rust-timeout-recovery",
        root,
    )
    _assert_recovery_parity("last-good-after-timeout", python_states[0], rust_states[0])
    _assert_recovery_parity("recovered", python_states[1], rust_states[1])


def _compare_scenario(
    scenario: Scenario,
    rust_binary: Path,
    root: Path,
) -> None:
    scenario_root = root / scenario.name
    gateway_dir = scenario_root / "gateway"
    scenario_root.mkdir(parents=True)
    if scenario.values is not None:
        _write_gateway_snapshot(
            gateway_dir,
            scenario.values,
            age_seconds=scenario.age_seconds,
            observed_epoch=scenario.observed_epoch,
        )
    else:
        gateway_dir.mkdir(parents=True)
    config_path = scenario_root / "config.ini"
    config_path.write_text(_base_config(gateway_dir) + scenario.config, encoding="utf-8")
    python_snapshot = scenario_root / "python.json"
    rust_snapshot = scenario_root / "rust.json"
    python_payload = _run_once(
        (sys.executable, str(REPO_ROOT / "venus_evcharger_auto_input_helper.py")),
        config_path,
        python_snapshot,
        f"python-{scenario.name}",
    )
    rust_payload = _run_once(
        (str(rust_binary),),
        config_path,
        rust_snapshot,
        f"rust-{scenario.name}",
    )
    result = compare_snapshots(python_payload, rust_payload)
    if not result.equal:
        details = "\n".join(f"  - {item}" for item in result.differences)
        raise RuntimeError(f"scenario {scenario.name} differs:\n{details}")
    if scenario.expected:
        _assert_expected(scenario.name, rust_payload, scenario.expected)


def _gateway_scenarios() -> tuple[Scenario, ...]:
    baseline = {
        "grid": -325.0,
        "pv": 2_750.0,
        "soc": 80.0,
        "battery_power": -900.0,
        "capacity_wh": 10_240.0,
        "capacity_ah": 200.0,
        "voltage": 51.2,
    }
    semantic_source = """AutoEnergySources=victron
AutoEnergySource.victron.Profile=dbus-hybrid
AutoEnergySource.victron.CapacityEstimatedWh=4800
AutoEnergySource.victron.CapacityEstimatedAh=100
AutoEnergySource.victron.CapacityEstimatedNominalVoltage=48
AutoEnergySource.victron.CapacityEstimatedCellCount=15
"""
    return (
        Scenario("gateway-fresh", values=baseline, expected={"pv_power": 2_750.0}),
        Scenario("gateway-missing", values=None, expected={"pv_power": None}),
        Scenario("gateway-stale", values=baseline, age_seconds=20.0, expected={"pv_power": None}),
        Scenario(
            "epoch-jump-monotonic-fresh",
            values=baseline,
            observed_epoch=1.0,
            expected={"pv_power": 2_750.0},
        ),
        Scenario(
            "capacity-live-wh",
            config=semantic_source,
            values=baseline,
            expected={"battery_combined_usable_capacity_wh": 10_240.0},
        ),
        Scenario(
            "capacity-configured-wh",
            config=semantic_source + "AutoEnergySource.victron.UsableCapacityWh=9000\n",
            values=baseline | {"capacity_wh": None},
            expected={"battery_combined_usable_capacity_wh": 9_000.0},
        ),
        Scenario(
            "capacity-persisted-estimate",
            config=semantic_source,
            values=baseline | {"capacity_wh": None, "capacity_ah": None, "voltage": None},
            expected={"battery_combined_usable_capacity_wh": 4_800.0},
        ),
        Scenario(
            "capacity-inferred-ah-voltage",
            config="\n".join(
                line
                for line in semantic_source.splitlines()
                if "CapacityEstimated" not in line
            )
            + "\n",
            values=baseline | {"capacity_wh": None, "soc": 98.0},
            expected={"battery_combined_usable_capacity_wh": 9_600.0},
        ),
    )


def _external_config(profile: str, connector_path: Path, *, pv_policy: str = "external_preferred") -> str:
    return f"""AutoEnergySources=victron,external
AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.UsableCapacityWh=5000
AutoEnergySource.external.Profile={profile}
AutoEnergySource.external.ConfigPath={connector_path}
AutoEnergySource.external.Service=differential-external
AutoPvSourcePolicy={pv_policy}
AutoPvExternalSource=external
"""


def _run_http_scenarios(rust_binary: Path, root: Path) -> None:
    payload = {
        "data": {
            "soc": 70.0,
            "capacity_wh": 8000.0,
            "battery_power_w": -600.0,
            "ac_power_w": 2200.0,
            "pv_w": 2400.0,
            "grid_w": -200.0,
            "mode": "hybrid",
            "online": True,
            "confidence": 0.8,
        }
    }
    with json_server(payload) as base_url:
        connector = root / "http-source.ini"
        connector.write_text(
            f"""[Adapter]
BaseUrl={base_url}
RequestTimeoutSeconds=0.25
[EnergyRequest]
Method=GET
Url=/energy
[EnergyResponse]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
BatteryPowerPath=data.battery_power_w
AcPowerPath=data.ac_power_w
PvInputPowerPath=data.pv_w
GridInteractionPath=data.grid_w
OperatingModePath=data.mode
OnlinePath=data.online
ConfidencePath=data.confidence
""",
            encoding="utf-8",
        )
        _compare_scenario(
            Scenario(
                "connector-http",
                config=_external_config("template-http-hybrid", connector),
                values={"grid": 250.0, "pv": 100.0, "soc": 60.0, "battery_power": 100.0},
                expected={"pv_power": 2_400.0},
            ),
            rust_binary,
            root,
        )


def _run_command_scenario(rust_binary: Path, root: Path) -> None:
    command = root / "energy-command"
    command.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"data\":{\"soc\":65,\"capacity_wh\":7000,\"battery_power_w\":-300,\"pv_w\":1800,\"online\":true,\"confidence\":0.9}}'\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    connector = root / "command-source.ini"
    connector.write_text(
        f"""[Command]
Args={command}
TimeoutSeconds=0.25
[Response]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
BatteryPowerPath=data.battery_power_w
PvInputPowerPath=data.pv_w
OnlinePath=data.online
ConfidencePath=data.confidence
""",
        encoding="utf-8",
    )
    _compare_scenario(
        Scenario(
            "connector-command-json",
            config=_external_config("command-json-hybrid", connector),
            values={"grid": 0.0, "pv": 50.0, "soc": 50.0, "battery_power": 0.0},
            expected={"pv_power": 1_800.0},
        ),
        rust_binary,
        root,
    )


def _run_opendtu_scenario(rust_binary: Path, root: Path) -> None:
    payload = {
        "inverters": [
            {
                "serial": "A",
                "reachable": True,
                "producing": True,
                "data_age": 1.0,
                "AC": {"0": {"Power": {"v": 1234.0}}},
                "DC": {"0": {"Power": {"v": 1300.0}}},
            }
        ]
    }
    with json_server(payload) as base_url:
        connector = root / "opendtu-source.ini"
        connector.write_text(
            f"""[Adapter]
BaseUrl={base_url}
RequestTimeoutSeconds=0.25
[OpenDTU]
StatusUrl=/status
InverterStatusUrl=/status?inv=${{serial}}
InverterSerials=A
MaxDataAgeSeconds=600
""",
            encoding="utf-8",
        )
        _compare_scenario(
            Scenario(
                "connector-opendtu",
                config=_external_config("opendtu-pvinverter", connector),
                values={"grid": 0.0, "pv": 25.0, "soc": 55.0, "battery_power": 0.0},
                expected={"pv_power": 1_300.0},
            ),
            rust_binary,
            root,
        )


def _run_modbus_scenario(rust_binary: Path, root: Path) -> None:
    with modbus_server() as (host, port):
        connector = root / "modbus-source.ini"
        connector.write_text(
            f"""[Transport]
Type=tcp
Host={host}
Port={port}
UnitId=7
RequestTimeoutSeconds=0.25
[SocRead]
RegisterType=holding
Address=10
DataType=uint16
Scale=0.1
""",
            encoding="utf-8",
        )
        _compare_scenario(
            Scenario(
                "connector-modbus",
                config=_external_config("modbus-hybrid", connector, pv_policy="gateway_only")
                + "AutoEnergySource.external.UsableCapacityWh=6000\n",
                values={"grid": 0.0, "pv": 500.0, "soc": 60.0, "battery_power": 0.0},
                expected={"pv_power": 500.0},
            ),
            rust_binary,
            root,
        )


def _run_grid_fusion_scenario(rust_binary: Path, root: Path) -> None:
    payload = {"data": {"soc": 70.0, "capacity_wh": 8000.0, "grid_w": -450.0, "online": True}}
    with json_server(payload) as base_url:
        connector = root / "grid-source.ini"
        connector.write_text(
            f"""[Adapter]
BaseUrl={base_url}
[EnergyRequest]
Url=/energy
[EnergyResponse]
SocPath=data.soc
UsableCapacityWhPath=data.capacity_wh
GridInteractionPath=data.grid_w
OnlinePath=data.online
""",
            encoding="utf-8",
        )
        config = _external_config("template-http-hybrid", connector, pv_policy="gateway_only") + """AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=external
AutoGridFusionBackupSource=victron
AutoGridFusionFailoverSamples=1
AutoGridFusionRecoverySamples=1
AutoGridFusionFailoverHoldSeconds=0
AutoGridFusionMismatchSamples=1
AutoGridFusionMismatchAbsoluteWatts=100
AutoGridFusionMismatchRelative=0
"""
        _compare_scenario(
            Scenario(
                "grid-fusion-hysteresis-boundaries",
                config=config,
                values={"grid": 250.0, "pv": 500.0, "soc": 60.0, "battery_power": 0.0},
                expected={"grid_power": 250.0, "grid_selected_source_id": "conservative"},
            ),
            rust_binary,
            root,
        )


def run(rust_binary: Path) -> None:
    if not rust_binary.is_file() or not os.access(rust_binary, os.X_OK):
        raise RuntimeError(f"Rust helper binary is not executable: {rust_binary}")
    with tempfile.TemporaryDirectory(prefix="auto-input-differential-") as temp_dir:
        root = Path(temp_dir)
        for scenario in _gateway_scenarios():
            _compare_scenario(scenario, rust_binary, root)
        _run_http_scenarios(rust_binary, root)
        _run_command_scenario(rust_binary, root)
        _run_opendtu_scenario(rust_binary, root)
        _run_modbus_scenario(rust_binary, root)
        _run_grid_fusion_scenario(rust_binary, root)
        _run_timeout_last_good_recovery(rust_binary, root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-binary", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args.rust_binary.resolve())
    except (OSError, RuntimeError, SnapshotParityError, subprocess.TimeoutExpired) as error:
        print(f"Auto-input Python/Rust differential failed: {error}", file=sys.stderr)
        return 1
    print("Auto-input Python/Rust differential scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
