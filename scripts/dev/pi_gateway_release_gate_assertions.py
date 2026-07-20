# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-visible gateway assertions for the Raspberry-Pi release gate."""

from __future__ import annotations

import shlex
import time

from pi_gateway_release_gate_common import GUI_PATHS, GateFailure, PiSession, json_object, object_dict


def assert_gui_values(pi: PiSession, service: str, run_dir: str, *, expect_power: bool) -> dict[str, float]:
    values = {path: _gateway_get(pi, run_dir, service, path) for path in GUI_PATHS}
    numeric = {path: _float_value(value) for path, value in values.items() if path not in {"/Mode"}}
    _assert_evcs_connected(values)
    if expect_power:
        _assert_power_values(numeric)
    return numeric


def exercise_gui_write(pi: PiSession, service: str, run_dir: str, remote_dir: str) -> None:
    original = _gateway_get(pi, run_dir, service, "/Mode")
    target = 1 if _float_value(original) != 1.0 else 0
    mode_target = (service, "/Mode")
    _enqueue_gateway_write(pi, remote_dir, run_dir, mode_target, target)
    _wait_for_gateway_value(pi, run_dir, mode_target, target, timeout=8.0)
    _enqueue_gateway_write(pi, remote_dir, run_dir, mode_target, original)
    _wait_for_gateway_value(pi, run_dir, mode_target, original, timeout=8.0)


def _gateway_get(pi: PiSession, run_dir: str, service: str, path: str) -> object:
    raw = pi.ssh(f"cat {(run_dir.rstrip('/') + '/dbus-cache.json')!r}", timeout=8.0)
    snapshot = json_object(raw, detail="gateway cache")
    values = object_dict(snapshot.get("values"))
    entry = object_dict(values.get(f"path:{service}{path}")) if values is not None else None
    if entry is None:
        raise GateFailure(f"gateway cache has no value for {service}{path}")
    if str(entry.get("status") or "") != "fresh":
        raise GateFailure(f"gateway cache value is unavailable for {service}{path}: {entry!r}")
    return entry.get("value")


def _enqueue_gateway_write(
    pi: PiSession,
    remote_dir: str,
    run_dir: str,
    target: tuple[str, str],
    value: object,
) -> None:
    service, path = target
    payload = {
        "kind": "set_value",
        "source": "pi-release-gate",
        "service": service,
        "path": path,
        "value": value,
        "priority": "user",
        "coalesce_key": f"release-gate:{service}:{path}",
    }
    code = (
        "import json;"
        "from venus_evcharger.dbus_gateway import GatewayClient,gateway_paths;"
        f"client=GatewayClient(gateway_paths({run_dir!r}),timeout_seconds=2.0);"
        f"print(json.dumps(client.send({payload!r})))"
    )
    raw = pi.ssh(f"cd {shlex.quote(remote_dir)} && python3 -c {shlex.quote(code)}", timeout=8.0)
    response = json_object(raw, detail="gateway write response")
    if response.get("ok") is not True:
        raise GateFailure(f"gateway rejected write for {service}{path}: {response!r}")


def _float_value(value: object) -> float:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        raise GateFailure(f"not a numeric gateway value: {value!r}")
    try:
        return float(value)
    except ValueError as error:
        raise GateFailure(f"not a numeric gateway value: {value!r}") from error


def _assert_evcs_connected(values: dict[str, object]) -> None:
    if int(float(values["/Connected"])) != 1:
        raise GateFailure(f"EVCS is not connected: /Connected={values['/Connected']}")


def _assert_power_values(numeric: dict[str, float]) -> None:
    for path, minimum, reason in (
        ("/Ac/Power", 500.0, "did not follow simulator"),
        ("/Ac/Current", 2.0, "did not follow simulator"),
        ("/Session/Time", 0.0, "did not advance"),
        ("/Session/Energy", 0.0, "did not advance"),
    ):
        _assert_minimum_value(numeric, path, minimum, reason)


def _assert_minimum_value(numeric: dict[str, float], path: str, minimum: float, reason: str) -> None:
    if numeric[path] <= minimum:
        raise GateFailure(f"{path} {reason}: {numeric[path]}")


def _wait_for_gateway_value(
    pi: PiSession,
    run_dir: str,
    target: tuple[str, str],
    expected: object,
    *,
    timeout: float,
) -> None:
    service, path = target
    deadline = time.time() + max(0.1, float(timeout))
    normalized_expected = str(expected).strip()
    last: object = ""
    while time.time() < deadline:
        last = _gateway_get(pi, run_dir, service, path)
        if str(last).strip() == normalized_expected:
            return
        time.sleep(0.5)
    raise GateFailure(f"{path} did not become {normalized_expected}; last={last!r}")
