#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway and connector scenarios for auto-input differential checks."""

from __future__ import annotations

from pathlib import Path

from scripts.dev.auto_input_helper_differential_harness import (
    Scenario,
    compare_scenario,
    external_config,
)
from scripts.dev.auto_input_helper_differential_servers import json_server, modbus_server


def gateway_scenarios() -> tuple[Scenario, ...]:
    """Return core gateway freshness and capacity-priority scenarios."""
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
                line for line in semantic_source.splitlines() if "CapacityEstimated" not in line
            )
            + "\n",
            values=baseline | {"capacity_wh": None, "soc": 98.0},
            expected={"battery_combined_usable_capacity_wh": 9_600.0},
        ),
    )


def _run_http_scenario(rust_binary: Path, root: Path) -> None:
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
        compare_scenario(
            Scenario(
                "connector-http",
                config=external_config("template-http-hybrid", connector),
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
    compare_scenario(
        Scenario(
            "connector-command-json",
            config=external_config("command-json-hybrid", connector),
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
        compare_scenario(
            Scenario(
                "connector-opendtu",
                config=external_config("opendtu-pvinverter", connector),
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
        compare_scenario(
            Scenario(
                "connector-modbus",
                config=external_config("modbus-hybrid", connector, pv_policy="gateway_only")
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
        config = external_config("template-http-hybrid", connector, pv_policy="gateway_only") + """AutoGridFusionEnabled=1
AutoGridFusionPrimarySource=external
AutoGridFusionBackupSource=victron
AutoGridFusionFailoverSamples=1
AutoGridFusionRecoverySamples=1
AutoGridFusionFailoverHoldSeconds=0
AutoGridFusionMismatchSamples=1
AutoGridFusionMismatchAbsoluteWatts=100
AutoGridFusionMismatchRelative=0
"""
        compare_scenario(
            Scenario(
                "grid-fusion-hysteresis-boundaries",
                config=config,
                values={"grid": 250.0, "pv": 500.0, "soc": 60.0, "battery_power": 0.0},
                expected={"grid_power": 250.0, "grid_selected_source_id": "conservative"},
            ),
            rust_binary,
            root,
        )


def run_connector_scenarios(rust_binary: Path, root: Path) -> None:
    """Run every external connector and grid-fusion parity scenario."""
    _run_http_scenario(rust_binary, root)
    _run_command_scenario(rust_binary, root)
    _run_opendtu_scenario(rust_binary, root)
    _run_modbus_scenario(rust_binary, root)
    _run_grid_fusion_scenario(rust_binary, root)
