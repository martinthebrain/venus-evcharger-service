#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process and snapshot harness for auto-input differential scenarios."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.dev.compare_auto_input_helper_snapshots import compare_snapshots, load_snapshot
from venus_evcharger.ipc.energy import EnergyInputsSnapshot, MeasuredValue
from venus_evcharger.ipc.energy_binary import write_energy_inputs_file

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESS_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True, slots=True)
class Scenario:
    """One deterministic input-helper parity scenario."""

    name: str
    config: str = ""
    values: Mapping[str, float | None] | None = None
    age_seconds: float = 0.0
    observed_epoch: float | None = None
    expected: Mapping[str, object] | None = None


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


def write_gateway_snapshot(
    run_dir: Path,
    values: Mapping[str, float | None],
    *,
    age_seconds: float,
    observed_epoch: float | None,
) -> None:
    """Write one versioned gateway input fixture."""
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


def base_config(run_dir: Path) -> str:
    """Return the shared bounded runtime configuration."""
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


def _assert_expected(
    name: str,
    payload: Mapping[str, object],
    expected: Mapping[str, object],
) -> None:
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(
                f"scenario {name} produced {key}={payload.get(key)!r}, expected {value!r}"
            )


def compare_scenario(scenario: Scenario, rust_binary: Path, root: Path) -> None:
    """Run one scenario through Python and Rust and compare snapshots."""
    scenario_root = root / scenario.name
    gateway_dir = scenario_root / "gateway"
    scenario_root.mkdir(parents=True)
    if scenario.values is not None:
        write_gateway_snapshot(
            gateway_dir,
            scenario.values,
            age_seconds=scenario.age_seconds,
            observed_epoch=scenario.observed_epoch,
        )
    else:
        gateway_dir.mkdir(parents=True)
    config_path = scenario_root / "config.ini"
    config_path.write_text(base_config(gateway_dir) + scenario.config, encoding="utf-8")
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


def external_config(
    profile: str,
    connector_path: Path,
    *,
    pv_policy: str = "external_preferred",
) -> str:
    """Return one external-source configuration fragment."""
    return f"""AutoEnergySources=victron,external
AutoEnergySource.victron.Profile=dbus-battery
AutoEnergySource.victron.UsableCapacityWh=5000
AutoEnergySource.external.Profile={profile}
AutoEnergySource.external.ConfigPath={connector_path}
AutoEnergySource.external.Service=differential-external
AutoPvSourcePolicy={pv_policy}
AutoPvExternalSource=external
"""
