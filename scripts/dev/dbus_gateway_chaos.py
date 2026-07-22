#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline chaos checks for the Venus EV charger DBus gateway.

The script never touches the real Victron DBus. It exercises the gateway
scheduler, cache, health, and command queues in a temporary runtime directory
so it can be run on a Pi/test-GX before trying changes on a real installation.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.ipc.command_mailbox import normalized_mapping
from venus_evcharger.ipc.gateway_publication import (
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_evcs_command,
)
from venus_evcharger.ports.gateway_publication import EvcsServiceIdentity

GUI_BURST_COMMAND_COUNT = 200
RESOURCE_PRESSURE_MIN_TICK_SECONDS = 0.3


class _FakeDbusService(dict[str, object]):
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        super().__init__()
        self.registered = False

    def register(self) -> None:
        self.registered = True

    def add_path(self, path: str, value: object, **_kwargs: object) -> None:
        self[path] = value


fake_vedbus = ModuleType("vedbus")
fake_vedbus.__dict__["VeDbusService"] = _FakeDbusService
sys.modules.setdefault("vedbus", fake_vedbus)

from venus_evcharger_dbus_adapter import DbusAdapter


def _adapter(temp_dir: str, extra_config: str = "") -> DbusAdapter:
    config_path = Path(temp_dir) / "config.ini"
    config_path.write_text(
        "[DEFAULT]\n"
        "DbusIntrospectionEnabled=0\n"
        "DbusGatewayLocalPublishBurstLimit=25\n"
        "DbusGatewayQueueBudgetGuiCriticalPublish=50\n"
        "DbusGatewayQueueBudgetLocalPublish=250\n"
        "DbusGatewayHealthLogIntervalSeconds=0.01\n"
        f"{extra_config}",
        encoding="utf-8",
    )
    return DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))


def _evcs_identity() -> EvcsServiceIdentity:
    return EvcsServiceIdentity(
        product_name="Chaos EVCS",
        custom_name="Chaos EVCS",
        firmware_version="test",
        hardware_version="simulated",
        serial="chaos-evcs",
        connection_name="Offline chaos harness",
        process_name="dbus_gateway_chaos.py",
        process_version="Python",
    )


def _register_evcs(adapter: DbusAdapter) -> None:
    outcome = adapter.write_scheduler.process_command(
        register_evcs_command(_evcs_identity(), {"connected": 1, "mode": 0})
    )
    _assert(outcome == "applied", f"semantic EVCS registration failed: {outcome}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def scenario_dbus_hang(temp_dir: str) -> None:
    adapter = _adapter(temp_dir)
    with (
        patch.object(adapter, "process_socket_once", return_value=None),
        patch.object(
            adapter,
            "process_one_dbus_operation_once",
            side_effect=TimeoutError("simulated 5s DBus hang"),
        ),
        patch.object(adapter, "publish_cache", return_value=None),
    ):
        _assert(bool(adapter.tick()), "tick should survive simulated DBus timeout")
    _assert(bool(adapter.circuit.last_error), "circuit should record the simulated DBus timeout")


def scenario_gui_burst(temp_dir: str) -> None:
    adapter = _adapter(temp_dir, "DbusGatewayLocalPublishTickBudgetMs=10\n")
    _register_evcs(adapter)
    for index in range(GUI_BURST_COMMAND_COUNT):
        adapter.commands.enqueue(publish_evcs_fields_command({"ac_power_w": index}, priority="live"))
    pending = adapter.commands.load_pending()
    _assert(len(pending) == 1, f"semantic latest-wins burst should coalesce to one command, got {len(pending)}")
    processed = adapter.write_scheduler.process_local_publish_burst(GUI_BURST_COMMAND_COUNT)
    _assert(
        processed == 1,
        f"coalesced GUI burst should require one scheduled publication, got {processed}",
    )
    _assert(not adapter.commands.load_pending(), "GUI burst queue should drain")
    observation = adapter.publication_registry.evcs_field_observation("ac_power_w")
    _assert(
        observation is not None and observation.value == GUI_BURST_COMMAND_COUNT - 1,
        "latest semantic EVCS value should win the coalesced burst",
    )


def scenario_core_overproduction(temp_dir: str) -> None:
    adapter = _adapter(temp_dir, "DbusGatewaySloQueueMaxAgeSeconds=1\n")
    now = time.time()
    for index in range(80):
        publication = publish_companion_fields_command(
            f"chaos-source-{index}",
            {"ac_power_w": index},
            priority="diagnostic",
        )
        publication["created_at"] = now - 5.0
        adapter.commands.enqueue(publication)
    health = adapter.health_snapshot()
    backpressure = normalized_mapping(health.get("backpressure"))
    _assert(
        backpressure is not None and backpressure.get("state") != "ok",
        "overproduction should trip backpressure",
    )


def scenario_reboot_mid_burst(temp_dir: str) -> None:
    paths = gateway_paths(str(Path(temp_dir) / "run"))
    first = _adapter(temp_dir)
    first.commands.enqueue(publish_evcs_fields_command({"mode": 1}, priority="critical"))
    second = DbusAdapter(first.config_path, paths=paths)
    _assert(bool(second.commands.load_pending()), "command should survive adapter restart")


def scenario_resource_pressure(temp_dir: str) -> None:
    adapter = _adapter(temp_dir, "DbusGatewaySloMainloopGapMaxMs=100\n")
    now = time.monotonic()
    adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=now - 1.0)
    adapter.tick_health.record(duration_ms=250.0, expected_interval_s=0.1, now=now)
    adapter.update_adaptive_tick()
    _assert(
        adapter.tick_seconds >= RESOURCE_PRESSURE_MIN_TICK_SECONDS,
        "long tick pressure should slow adaptive tick",
    )


def run() -> dict[str, str]:
    scenarios = {
        "dbus-hang": scenario_dbus_hang,
        "gui-burst": scenario_gui_burst,
        "core-overproduction": scenario_core_overproduction,
        "reboot-mid-burst": scenario_reboot_mid_burst,
        "resource-pressure": scenario_resource_pressure,
    }
    results: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="evcharger-gateway-chaos-") as temp_dir:
        for name, scenario in scenarios.items():
            scenario_dir = str(Path(temp_dir) / name)
            Path(scenario_dir).mkdir()
            scenario(scenario_dir)
            results[name] = "ok"
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline DBus gateway chaos checks.")
    parser.parse_args()
    print(json.dumps({"ok": True, "results": run()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
