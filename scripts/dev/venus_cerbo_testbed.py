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
    DbusCacheStore,
    GatewayClient,
    dbus_path_key,
    gateway_paths,
)


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

CERBO_READ_ONLY_PROBES = (
    ("com.victronenergy.platform", "/Relay/0/State"),
    ("com.victronenergy.platform", "/Relay/1/State"),
    ("com.victronenergy.settings", "/Settings/Relay/0/Function"),
    ("com.victronenergy.settings", "/Settings/Relay/1/Function"),
)


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
    response = client.send({"kind": "health", "source": "venus-cerbo-testbed"})
    if response.get("ok") is not True:
        return _gateway_unavailable_payload(response)
    probes = [_read_gateway_value(client, service, path, timeout) for service, path in CERBO_READ_ONLY_PROBES]
    return _real_probe_payload(probes)


def _gateway_unavailable_payload(response: dict[str, object]) -> dict[str, Any]:
    return {
        "ok": False,
        "kind": "venus-cerbo-testbed",
        "mode": "probe-real",
        "skipped": True,
        "reason": f"DBus gateway unavailable: {response.get('error') or 'no response'}",
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


def _read_gateway_value(client: GatewayClient, service: str, path: str, timeout: float) -> dict[str, Any]:
    requested_at = time.time()
    response = client.send(
        {
            "kind": "refresh_value",
            "source": "venus-cerbo-testbed",
            "service": service,
            "path": path,
            "priority": "diagnostic",
            "reason": "probe-real",
            "coalesce_key": f"cerbo-probe:{service}:{path}",
        }
    )
    if response.get("ok") is not True:
        return _probe_result(service, path, ok=False, skipped=False, error=str(response.get("error") or "rejected"))
    return _wait_for_gateway_probe(client, service, path, timeout, requested_at=requested_at)


def _wait_for_gateway_probe(
    client: GatewayClient,
    service: str,
    path: str,
    timeout: float,
    *,
    requested_at: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.1, float(timeout))
    key = dbus_path_key(service, path)
    while time.monotonic() < deadline:
        entry = DbusCacheStore.value_entry(client.load_cache(max_age_seconds=max(1.0, timeout * 2.0)), key)
        if entry is not None:
            result = _probe_result_from_entry(service, path, entry, requested_at=requested_at)
            if result is not None:
                return result
        time.sleep(0.05)
    return _probe_result(service, path, ok=False, skipped=False, error="timeout")


def _probe_result_from_entry(
    service: str,
    path: str,
    entry: dict[str, object],
    *,
    requested_at: float,
) -> dict[str, Any] | None:
    status = str(entry.get("status") or "")
    completed_at = max(_float_value(entry.get("confirmed_at")), _float_value(entry.get("error_at")))
    if completed_at < requested_at:
        return None
    return _completed_probe_result(service, path, entry, status)


def _completed_probe_result(
    service: str,
    path: str,
    entry: dict[str, object],
    status: str,
) -> dict[str, Any] | None:
    if status in {"fresh", "stale"}:
        return _probe_result(service, path, ok=True, skipped=False, value=entry.get("value"))
    if status in {"error", "unavailable"}:
        return _probe_result(
            service,
            path,
            ok=False,
            skipped=True,
            error=str(entry.get("last_error") or status),
        )
    return None


def _float_value(value: object) -> float:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


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
