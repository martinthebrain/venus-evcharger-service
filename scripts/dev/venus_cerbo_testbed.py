#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small Venus OS/Cerbo live-testbed helper.

The default mode is intentionally CI-safe: it returns deterministic DBus-like
service snapshots that model common EV-charger scenarios. On a real Venus OS
device, ``probe-real`` requests Cerbo relay paths through the DBus gateway.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from venus_evcharger.dbus_gateway import (
    DEFAULT_GATEWAY_RUN_DIR,
    GatewayClient,
    GatewayOperationsClient,
    gateway_paths,
)
from venus_evcharger.ports.gateway_operations import GatewayOperationsPort


@dataclass(frozen=True, slots=True)
class SimulatedDbusValue:
    service: str
    path: str
    value: object


SIMULATED_SCENARIOS: dict[str, tuple[SimulatedDbusValue, ...]] = {
    "pv-surplus": (
        SimulatedDbusValue("com.victronenergy.system", "/Dc/Battery/Soc", 74.0),
        SimulatedDbusValue("com.victronenergy.system", "/Ac/Grid/L1/Power", -420.0),
        SimulatedDbusValue("com.victronenergy.pvinverter.http_48", "/Ac/Power", 2680.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Mode", 1),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/StartStop", 1),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionReason", "pv-surplus"),
    ),
    "night-fallback": (
        SimulatedDbusValue("com.victronenergy.system", "/Dc/Battery/Soc", 63.0),
        SimulatedDbusValue("com.victronenergy.system", "/Ac/Grid/L1/Power", 1800.0),
        SimulatedDbusValue("com.victronenergy.pvinverter.http_48", "/Ac/Power", 0.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Mode", 2),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/ScheduledState", "night-boost"),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionReason", "scheduled-night-charge"),
    ),
    "unplug-replug": (
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Mode", 2),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/StartStop", 1),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Ac/Power", 0.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Session/Energy", 0.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionReason", "vehicle-not-charging"),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionRelayIntent", 1),
    ),
}

CERBO_RELAY_PROBES = (0, 1)


def simulated_payload(scenario: str) -> dict[str, Any]:
    values = SIMULATED_SCENARIOS[scenario]
    return {
        "ok": True,
        "kind": "venus-cerbo-testbed",
        "mode": "simulate",
        "scenario": scenario,
        "services": [asdict(value) for value in values],
        "expectations": scenario_expectations(scenario),
    }


def scenario_expectations(scenario: str) -> dict[str, Any]:
    return {
        "pv-surplus": {
            "mode": 1,
            "should_charge_when_thresholds_allow": True,
            "primary_reason": "pv-surplus",
        },
        "night-fallback": {
            "mode": 2,
            "should_charge_after_day_window": True,
            "primary_reason": "scheduled-night-charge",
        },
        "unplug-replug": {
            "session_energy_should_reset": True,
            "gui_should_remain_writable": True,
            "mode_should_remain": 2,
        },
    }[scenario]


def probe_real_cerbo(timeout: float, gateway_run_dir: str = DEFAULT_GATEWAY_RUN_DIR) -> dict[str, Any]:
    client = GatewayClient(gateway_paths(gateway_run_dir), timeout_seconds=min(max(0.1, timeout), 2.0))
    health = client.load_health(max_age_seconds=max(1.0, timeout * 2.0))
    if not health:
        return _gateway_unavailable_payload()
    operations = GatewayOperationsClient(client)
    probes = [_read_gateway_relay(operations, relay_index, timeout) for relay_index in CERBO_RELAY_PROBES]
    return _real_probe_payload(probes)


def _gateway_unavailable_payload() -> dict[str, Any]:
    return {
        "ok": False,
        "kind": "venus-cerbo-testbed",
        "mode": "probe-real",
        "skipped": True,
        "reason": "DBus gateway unavailable: health snapshot missing or stale",
        "probes": [],
    }


def _real_probe_payload(probes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": all(probe["ok"] or probe["skipped"] for probe in probes),
        "kind": "venus-cerbo-testbed",
        "mode": "probe-real",
        "skipped": False,
        "probes": probes,
    }


def _read_gateway_relay(
    operations: GatewayOperationsPort,
    relay_index: int,
    timeout: float,
) -> dict[str, Any]:
    service = "com.victronenergy.platform"
    path = f"/Relay/{relay_index}/State"
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        state = operations.read_gx_relay_state(
            relay_index,
            max_age_seconds=max(1.0, timeout * 2.0),
        )
        if state is not None:
            return _probe_result(service, path, ok=True, skipped=False, value=state)
        time.sleep(0.05)
    return _probe_result(
        service,
        path,
        ok=False,
        skipped=True,
        error="relay-state-unavailable",
    )


def _probe_result(
    service: str,
    path: str,
    *,
    ok: bool,
    skipped: bool,
    value: object = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "service": service,
        "path": path,
        "ok": ok,
        "skipped": skipped,
        "value": value,
        "error": error,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Venus OS/Cerbo EV charger live-testbed helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate", help="Print one deterministic DBus-like test scenario.")
    simulate.add_argument("scenario", choices=tuple(sorted(SIMULATED_SCENARIOS)))

    probe = subparsers.add_parser("probe-real", help="Request read-only Cerbo relay probes through the gateway.")
    probe.add_argument("--timeout", type=float, default=3.0, help="Per-probe timeout in seconds.")
    probe.add_argument("--gateway-run-dir", default=DEFAULT_GATEWAY_RUN_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(list(argv) if argv is not None else None)
    if namespace.command == "simulate":
        payload = simulated_payload(namespace.scenario)
    else:
        payload = probe_real_cerbo(namespace.timeout, namespace.gateway_run_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") or payload.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
