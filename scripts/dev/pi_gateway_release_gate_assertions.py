# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-visible DBus assertions for the Raspberry-Pi release gate."""

from __future__ import annotations

import time

from pi_gateway_release_gate_common import GUI_PATHS, GateFailure, PiSession


def assert_gui_values(pi: PiSession, service: str, *, expect_power: bool) -> dict[str, float]:
    values = {path: _dbus_get(pi, service, path) for path in GUI_PATHS}
    numeric = {path: _float_value(value) for path, value in values.items() if path not in {"/Mode"}}
    _assert_evcs_connected(values)
    if expect_power:
        _assert_power_values(numeric)
    return numeric


def exercise_gui_write(pi: PiSession, service: str) -> None:
    original = _dbus_get(pi, service, "/Mode")
    target = "1" if str(original).strip() != "1" else "0"
    pi.ssh(f"dbus -y {service!r} /Mode SetValue {target}", timeout=8.0)
    _wait_for_dbus_value(pi, service, "/Mode", target, timeout=8.0)
    pi.ssh(f"dbus -y {service!r} /Mode SetValue {original}", timeout=8.0)
    _wait_for_dbus_value(pi, service, "/Mode", original, timeout=8.0)


def _dbus_get(pi: PiSession, service: str, path: str) -> str:
    return pi.ssh(f"dbus -y {service!r} {path!r} GetValue", timeout=8.0).strip()


def _float_value(value: str) -> float:
    try:
        return float(value)
    except ValueError as error:
        raise GateFailure(f"not a numeric DBus value: {value!r}") from error


def _assert_evcs_connected(values: dict[str, str]) -> None:
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


def _wait_for_dbus_value(pi: PiSession, service: str, path: str, expected: str, *, timeout: float) -> None:
    deadline = time.time() + max(0.1, float(timeout))
    normalized_expected = str(expected).strip()
    last = ""
    while time.time() < deadline:
        last = _dbus_get(pi, service, path)
        if str(last).strip() == normalized_expected:
            return
        time.sleep(0.5)
    raise GateFailure(f"{path} did not become {normalized_expected}; last={last!r}")
