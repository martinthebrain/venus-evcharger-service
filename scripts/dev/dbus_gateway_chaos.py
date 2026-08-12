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
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from venus_evcharger.dbus_adapter.async_broker import DbusAsyncOperation, DbusAsyncTimeoutError
from venus_evcharger.dbus_adapter.tick_policy import TickDemand
from venus_evcharger.dbus_gateway import gateway_paths
from venus_evcharger.energy.grid_fusion import GridMeasurementFusion
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig, GridMeasurement
from venus_evcharger.energy.timestamped_measurement import TimestampedMeasurement
from venus_evcharger.ipc.command_mailbox import normalized_mapping
from venus_evcharger.ipc.gateway_publication import (
    publish_companion_fields_command,
    publish_evcs_fields_command,
    register_evcs_command,
)
from venus_evcharger.ports.gateway_publication import EvcsServiceIdentity

GUI_BURST_COMMAND_COUNT = 200
RESOURCE_PRESSURE_MIN_TICK_SECONDS = 0.3
MAX_ASYNC_SUBMIT_TICK_SECONDS = 0.25
CONSERVATIVE_GRID_POWER_W = -100.0


class _PendingCall:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


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
    pending_call = _PendingCall()
    reply_handlers: list[Callable[[object], None]] = []
    errors: list[BaseException] = []
    submitted = False

    def start_hanging_operation(
        reply: Callable[[object], None],
        _error: Callable[[BaseException], None],
    ) -> _PendingCall:
        reply_handlers.append(reply)
        return pending_call

    operation = DbusAsyncOperation(
        rate_kind="read",
        metric_kind="read",
        source="chaos-hanging-read",
        priority="normal",
        timeout_seconds=5.0,
        starter=start_hanging_operation,
        on_success=lambda _value: None,
        on_error=errors.append,
    )

    def submit_once() -> bool:
        nonlocal submitted
        if submitted:
            return False
        adapter.operation_broker.submit(operation)
        submitted = True
        return True

    with (
        patch.object(
            adapter.loop_role,
            "process_one_dbus_operation_once",
            side_effect=submit_once,
        ),
        patch.object(adapter.io_role, "publish_cache", return_value=None),
    ):
        started_at = time.monotonic()
        _assert(bool(adapter.tick()), "tick should survive simulated DBus timeout")
        _assert(
            time.monotonic() - started_at < MAX_ASYNC_SUBMIT_TICK_SECONDS,
            "submitting a hanging DBus call must not block the gateway tick",
        )
        _assert(adapter.operation_broker.busy, "hanging DBus call should occupy the single async slot")
        _assert(
            adapter.operation_broker.expire_due(now=time.monotonic() + 6.0),
            "hanging DBus call should expire at its monotonic deadline",
        )
        _assert(bool(adapter.tick()), "tick should continue after broker timeout")
    _assert(pending_call.cancelled, "expired DBus PendingCall should be cancelled")
    _assert(len(errors) == 1 and isinstance(errors[0], DbusAsyncTimeoutError), "timeout callback should run once")
    _assert(bool(adapter.circuit.last_error), "circuit should record the simulated DBus timeout")
    _assert(len(reply_handlers) == 1, "hanging operation should install exactly one reply callback")
    reply_handlers[0](True)
    broker_health = adapter.operation_broker.health()
    _assert(broker_health.get("late_replies") == 1, "late DBus replies should be ignored and counted")


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
    adapter.loop_role.update_adaptive_tick()
    _assert(
        adapter.tick_seconds >= RESOURCE_PRESSURE_MIN_TICK_SECONDS,
        "long tick pressure should slow adaptive tick",
    )
    idle_constrained = adapter.loop_role.adaptive_tick_seconds(
        circuit_state="ok",
        resource_state="constrained",
    )
    urgent_constrained = adapter.loop_role.adaptive_tick_seconds(
        circuit_state="ok",
        resource_state="constrained",
        demand=TickDemand(
            critical_read_operations=2,
            core_read_age_seconds=adapter.slo_core_read_max_age_seconds - 1.0,
            operation_p95_ms=100.0,
        ),
    )
    _assert(
        idle_constrained == adapter.max_tick_seconds,
        "idle constrained gateway should use its low-CPU cadence",
    )
    _assert(
        adapter.min_tick_seconds <= urgent_constrained < idle_constrained,
        "critical SLO demand should accelerate a constrained gateway",
    )


def scenario_epoch_clock_jump(_temp_dir: str) -> None:
    config = GridFusionConfig(
        enabled=True,
        primary_source_id="primary",
        backup_source_id="gateway",
        primary_max_age_seconds=5.0,
        backup_max_age_seconds=5.0,
    )
    primary = _grid_measurement("primary", 120.0, captured_at=1_000.0, monotonic_at=99.0)
    backup_before = _grid_measurement("gateway", 110.0, captured_at=2_000.0, monotonic_at=99.0)
    backup_after = _grid_measurement("gateway", 110.0, captured_at=10.0, monotonic_at=99.0)

    before = GridMeasurementFusion(config).resolve(primary, backup_before, 100.0)
    after = GridMeasurementFusion(config).resolve(primary, backup_after, 100.0)

    _assert(
        (before.state, before.power_w, before.backup_age_seconds)
        == (after.state, after.power_w, after.backup_age_seconds),
        "epoch clock jumps must not change grid freshness or source selection",
    )


def scenario_competing_grid_sources(_temp_dir: str) -> None:
    fusion = GridMeasurementFusion(
        GridFusionConfig(
            enabled=True,
            primary_source_id="primary",
            backup_source_id="gateway",
            primary_max_age_seconds=5.0,
            backup_max_age_seconds=5.0,
            failover_samples=2,
            recovery_samples=2,
            mismatch_absolute_watts=50.0,
            mismatch_relative=0.0,
            mismatch_samples=2,
        )
    )
    primary = _grid_measurement("primary", -900.0, captured_at=100.0, monotonic_at=100.0)
    backup = _grid_measurement(
        "gateway",
        CONSERVATIVE_GRID_POWER_W,
        captured_at=100.0,
        monotonic_at=100.0,
    )

    first = fusion.resolve(primary, backup, 100.0)
    disagreement = fusion.resolve(primary, backup, 100.1)
    _assert(first.state == "primary", "one mismatch sample must not switch the selected source")
    _assert(
        disagreement.state == "disagreement"
        and disagreement.power_w == CONSERVATIVE_GRID_POWER_W,
        "persistent source disagreement must choose the conservative grid value",
    )

    missing = GridMeasurement(
        source_id="primary",
        measurement=TimestampedMeasurement.unavailable(),
        online=False,
        confidence=0.0,
    )
    fusion.resolve(missing, backup, 101.0)
    failed_over = fusion.resolve(missing, backup, 101.1)
    _assert(
        failed_over.state == "backup" and failed_over.selected_source_id == "gateway",
        "confirmed primary loss must fail over to the healthy gateway source",
    )


def _grid_measurement(
    source_id: str,
    power_w: float,
    *,
    captured_at: float,
    monotonic_at: float,
) -> GridMeasurement:
    return GridMeasurement(
        source_id=source_id,
        measurement=TimestampedMeasurement.observed(
            power_w,
            captured_at=captured_at,
            observed_monotonic=monotonic_at,
        ),
    )


def run() -> dict[str, str]:
    scenarios = {
        "dbus-hang": scenario_dbus_hang,
        "gui-burst": scenario_gui_burst,
        "core-overproduction": scenario_core_overproduction,
        "reboot-mid-burst": scenario_reboot_mid_burst,
        "resource-pressure": scenario_resource_pressure,
        "epoch-clock-jump": scenario_epoch_clock_jump,
        "competing-grid-sources": scenario_competing_grid_sources,
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
