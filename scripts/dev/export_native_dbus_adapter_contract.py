#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export the authoritative gateway publication surface for native runtimes."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _install_vedbus_import_stub() -> None:
    """Permit schema imports on development hosts without Victron libraries."""
    if "vedbus" in sys.modules:
        return
    module = types.ModuleType("vedbus")
    module.VeDbusService = type("VeDbusService", (), {})
    sys.modules["vedbus"] = module


_install_vedbus_import_stub()

from venus_evcharger.dbus_adapter.publication.schema import (  # isort: skip
    COMPANION_PUBLICATION_SPECS,
    EVCS_PUBLICATION_SPECS,
)
from venus_evcharger.dbus_gateway_surface import (  # isort: skip
    evcs_path_freshness_kind,
    venus_control_route,
)
from venus_evcharger.dbus_adapter.health.slo import (  # isort: skip
    SloThresholds,
    effective_gui_max_age_seconds,
    slo_checks_from_observed,
    slo_targets,
)
from venus_evcharger.dbus_adapter.read.pv_dormancy import (  # isort: skip
    explicit_dormancy_error,
)
from venus_evcharger.dbus_adapter.tick_policy import (  # isort: skip
    TickDemand,
    TickPolicy,
    adaptive_tick_seconds,
)
from venus_evcharger.dbus_gateway_policy import (  # isort: skip
    command_allowed_by_backpressure,
    command_queue_class,
)


def _path_spec(
    spec: object,
    *,
    route: object | None = None,
    freshness_kind: str = "local_owned",
) -> dict[str, object]:
    formatter = getattr(spec, "formatter")
    route_payload = None
    if route is not None:
        route_payload = {
            "name": getattr(route, "name"),
            "target": getattr(route, "target"),
        }
    return {
        "path": getattr(spec, "path"),
        "default": getattr(spec, "default"),
        "writeable": bool(getattr(spec, "writeable")),
        "formatter": None if formatter is None else formatter.__name__.removeprefix("format_"),
        "route": route_payload,
        "freshness_kind": freshness_kind,
    }


def contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "evcs": {
            field: _path_spec(
                spec,
                route=venus_control_route(spec.path),
                freshness_kind=evcs_path_freshness_kind(spec.path),
            )
            for field, spec in sorted(EVCS_PUBLICATION_SPECS.items())
        },
        "companion": {
            kind: {
                field: _path_spec(spec)
                for field, spec in sorted(specs.items())
            }
            for kind, specs in sorted(COMPANION_PUBLICATION_SPECS.items())
        },
    }


def runtime_policy_contract() -> dict[str, object]:
    """Return differential scenarios shared by Python and the native adapter."""
    command_cases = [
        {"name": "registration", "command": {"kind": "register_evcs"}},
        {
            "name": "critical-publication",
            "command": {
                "kind": "publish_evcs_fields",
                "publication_priority": "critical",
                "priority": "user",
            },
        },
        {
            "name": "local-publication",
            "command": {"kind": "publish_companion_fields", "priority": "publish"},
        },
        {
            "name": "topology-refresh",
            "command": {
                "kind": "refresh_energy_inputs",
                "scope": "topology",
                "priority": "discovery",
            },
        },
        {
            "name": "remote-write",
            "command": {"kind": "gx_relay_set_enabled", "priority": "user"},
        },
        {
            "name": "diagnostic",
            "command": {"kind": "unknown", "priority": "diagnostic"},
        },
    ]
    pressure_states = ("ok", "congested", "slow", "protective")
    commands = [
        {
            **case,
            "queue_class": command_queue_class(case["command"]),
            "allowed": {
                state: command_allowed_by_backpressure(case["command"], state)
                for state in pressure_states
            },
        }
        for case in command_cases
    ]

    policy = TickPolicy(
        min_tick_seconds=0.2,
        max_tick_seconds=1.0,
        core_read_slo_seconds=5.0,
        queue_slo_seconds=10.0,
    )
    tick_inputs = [
        ("idle", TickDemand(), "ok", "ok"),
        ("busy-idle", TickDemand(), "ok", "busy"),
        ("degraded-idle", TickDemand(), "degraded", "ok"),
        (
            "read-deadline",
            TickDemand(
                critical_read_operations=2,
                core_read_age_seconds=4.5,
                operation_p95_ms=50.0,
            ),
            "ok",
            "busy",
        ),
        (
            "queue-deadline",
            TickDemand(
                critical_queue_operations=3,
                queue_age_seconds=9.5,
                operation_p95_ms=40.0,
            ),
            "ok",
            "constrained",
        ),
        ("protective", TickDemand(critical_read_operations=1), "protective", "ok"),
    ]
    ticks = [
        {
            "name": name,
            "demand": {
                "critical_read_operations": demand.critical_read_operations,
                "critical_queue_operations": demand.critical_queue_operations,
                "core_read_age_seconds": demand.core_read_age_seconds,
                "queue_age_seconds": demand.queue_age_seconds,
                "operation_p95_ms": demand.operation_p95_ms,
            },
            "circuit_state": circuit_state,
            "resource_state": resource_state,
            "tick_seconds": adaptive_tick_seconds(
                policy,
                demand,
                circuit_state=circuit_state,
                resource_state=resource_state,
            ),
        }
        for name, demand, circuit_state, resource_state in tick_inputs
    ]

    thresholds = SloThresholds(
        gui_max_age_seconds=2.0,
        core_read_max_age_seconds=5.0,
        queue_max_age_seconds=10.0,
        mainloop_gap_max_ms=500.0,
        publication_scheduler_tolerance_seconds=0.2,
    )
    observed = {
        "gui_max_age_s": 1.0,
        "gui_measurement_max_age_s": 1.0,
        "gui_control_max_age_s": 1.0,
        "gui_session_max_age_s": 1.0,
        "gui_missing_field_count": 0.0,
        "gui_measurement_missing_field_count": 0.0,
        "gui_control_missing_field_count": 0.0,
        "gui_session_missing_field_count": 0.0,
        "core_read_max_age_s": 1.0,
        "core_read_missing_count": 0.0,
        "core_read_nonfresh_count": 0.0,
        "queue_oldest_age_s": 1.0,
        "mainloop_max_gap_ms_60s": 10.0,
    }
    dormancy_messages = (
        "NoReply: inverter asleep",
        "source is in standby",
        "source is not asleep",
        "temporary timeout",
        "",
    )
    return {
        "schema_version": 1,
        "commands": commands,
        "tick_policy": {
            "policy": {
                "min_tick_seconds": policy.min_tick_seconds,
                "max_tick_seconds": policy.max_tick_seconds,
                "core_read_slo_seconds": policy.core_read_slo_seconds,
                "queue_slo_seconds": policy.queue_slo_seconds,
            },
            "cases": ticks,
        },
        "slo": {
            "thresholds": {
                "gui_max_age_seconds": thresholds.gui_max_age_seconds,
                "core_read_max_age_seconds": thresholds.core_read_max_age_seconds,
                "queue_max_age_seconds": thresholds.queue_max_age_seconds,
                "mainloop_gap_max_ms": thresholds.mainloop_gap_max_ms,
                "publication_scheduler_tolerance_seconds": (
                    thresholds.publication_scheduler_tolerance_seconds
                ),
            },
            "effective_gui_max_age_seconds": effective_gui_max_age_seconds(thresholds),
            "observed": observed,
            "checks": slo_checks_from_observed(observed, thresholds),
            "targets": slo_targets(thresholds),
        },
        "dormancy_messages": [
            {"message": message, "explicit": explicit_dormancy_error(message)}
            for message in dormancy_messages
        ],
    }


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "output",
        nargs="?",
        default="rust/dbus-adapter/contracts/publication.json",
    )
    parser.add_argument(
        "--runtime-output",
        default="rust/dbus-adapter/contracts/runtime_policy.json",
    )
    return parser.parse_args()


def _contract_outputs(arguments: argparse.Namespace) -> tuple[tuple[Path, dict[str, object]], ...]:
    return (
        (Path(arguments.output), contract()),
        (Path(arguments.runtime_output), runtime_policy_contract()),
    )


def _render(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _check_outputs(outputs: tuple[tuple[Path, dict[str, object]], ...]) -> int:
    for path, payload in outputs:
        if not path.is_file() or path.read_text(encoding="utf-8") != _render(payload):
            print(f"Native DBus adapter contract is stale: {path}", file=sys.stderr)
            return 1
    return 0


def _write_outputs(outputs: tuple[tuple[Path, dict[str, object]], ...]) -> None:
    for path, payload in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(payload), encoding="utf-8")


def main() -> int:
    arguments = _parse_arguments()
    outputs = _contract_outputs(arguments)
    if arguments.check:
        return _check_outputs(outputs)
    _write_outputs(outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
