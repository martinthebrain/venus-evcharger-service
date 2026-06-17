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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from venus_evcharger.dbus_gateway import gateway_paths

GUI_BURST_COMMAND_COUNT = 200
GUI_BURST_DRAIN_TICKS = 50
RESOURCE_PRESSURE_MIN_TICK_SECONDS = 0.3


class _FakeDbusService(dict[str, Any]):
    def __init__(self) -> None:
        super().__init__()
        self.registered = False

    def register(self) -> None:
        self.registered = True

    def add_path(self, path: str, value: Any, **_kwargs: Any) -> None:
        self[path] = value


fake_vedbus = ModuleType("vedbus")
fake_vedbus.VeDbusService = _FakeDbusService
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
    adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
    adapter._dbusservice = _FakeDbusService()
    adapter._dbusservice_registered = True
    return adapter


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def scenario_dbus_hang(temp_dir: str) -> None:
    adapter = _adapter(temp_dir)
    adapter._process_socket_once = lambda: None
    adapter._process_one_dbus_operation_once = lambda: (_ for _ in ()).throw(TimeoutError("simulated 5s DBus hang"))
    adapter._publish_cache = lambda: None
    _assert(adapter._tick(), "tick should survive simulated DBus timeout")
    _assert(adapter.circuit.last_error, "circuit should record the simulated DBus timeout")


def scenario_gui_burst(temp_dir: str) -> None:
    adapter = _adapter(temp_dir, "DbusGatewayLocalPublishTickBudgetMs=10\n")
    for index in range(GUI_BURST_COMMAND_COUNT):
        path = f"/Chaos/{index}"
        adapter.write_scheduler.registered_paths.add(path)
        adapter.commands.enqueue(
            {
                "kind": "publish_value",
                "path": path,
                "value": index,
                "priority": "publish",
                "coalesce_key": f"publish:{path}",
            }
        )
    first_tick = adapter.write_scheduler.process_local_publish_burst(GUI_BURST_COMMAND_COUNT)
    _assert(
        0 < first_tick < GUI_BURST_COMMAND_COUNT,
        f"first GUI burst tick should be bounded, got {first_tick}",
    )
    processed = first_tick
    for _index in range(GUI_BURST_DRAIN_TICKS):
        if not adapter.commands.load_pending():
            break
        processed += adapter.write_scheduler.process_local_publish_burst(GUI_BURST_COMMAND_COUNT)
    _assert(
        processed == GUI_BURST_COMMAND_COUNT,
        f"expected {GUI_BURST_COMMAND_COUNT} GUI publishes across bounded ticks, got {processed}",
    )
    _assert(not adapter.commands.load_pending(), "GUI burst queue should drain")


def scenario_core_overproduction(temp_dir: str) -> None:
    adapter = _adapter(temp_dir, "DbusGatewaySloQueueMaxAgeSeconds=1\n")
    now = time.time()
    for index in range(80):
        adapter.commands.enqueue(
            {
                "kind": "publish_value",
                "path": f"/Optional/{index}",
                "value": index,
                "priority": "diagnostic",
                "created_at": now - 5.0,
                "coalesce_key": f"publish:/Optional/{index}",
            }
        )
    health = adapter._health_snapshot()
    _assert(health["backpressure"]["state"] != "ok", "overproduction should trip backpressure")


def scenario_reboot_mid_burst(temp_dir: str) -> None:
    paths = gateway_paths(str(Path(temp_dir) / "run"))
    first = _adapter(temp_dir)
    first.commands.enqueue({"kind": "publish_value", "path": "/Mode", "value": 1, "coalesce_key": "publish:/Mode"})
    second = DbusAdapter(first.config_path, paths=paths)
    _assert(second.commands.load_pending(), "command should survive adapter restart")


def scenario_resource_pressure(temp_dir: str) -> None:
    adapter = _adapter(temp_dir, "DbusGatewaySloMainloopGapMaxMs=100\n")
    now = time.monotonic()
    adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=now - 1.0)
    adapter.tick_health.record(duration_ms=250.0, expected_interval_s=0.1, now=now)
    adapter._last_resource_snapshot = {"state": "ok"}
    adapter._update_adaptive_tick()
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
