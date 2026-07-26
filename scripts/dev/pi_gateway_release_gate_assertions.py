# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-visible gateway assertions for the Raspberry-Pi release gate."""

from __future__ import annotations

import shlex
import time

from pi_gateway_release_gate_common import GUI_PATHS, GateFailure, PiSession, json_object, object_dict

GUI_ASSERT_POLL_SECONDS = 0.5


def assert_gui_values(
    pi: PiSession,
    service: str,
    run_dir: str,
    *,
    expect_power: bool,
    wait_seconds: float = 0.0,
) -> dict[str, float]:
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while True:
        numeric, error = _gui_values_attempt(pi, service, run_dir, expect_power=expect_power)
        if numeric is not None:
            return numeric
        if not _wait_for_gui_retry(deadline):
            assert error is not None
            raise error


def _gui_values_attempt(
    pi: PiSession,
    service: str,
    run_dir: str,
    *,
    expect_power: bool,
) -> tuple[dict[str, float] | None, GateFailure | None]:
    try:
        return _validated_gui_values(pi, service, run_dir, expect_power=expect_power), None
    except GateFailure as error:
        return None, error


def _validated_gui_values(
    pi: PiSession,
    service: str,
    run_dir: str,
    *,
    expect_power: bool,
) -> dict[str, float]:
    values = {path: _gateway_get(pi, run_dir, service, path) for path in GUI_PATHS}
    numeric = {path: _float_value(value) for path, value in values.items() if path != "/Mode"}
    _assert_evcs_connected(values)
    if expect_power:
        _assert_power_values(numeric)
    return numeric


def _wait_for_gui_retry(deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        return False
    time.sleep(min(GUI_ASSERT_POLL_SECONDS, remaining))
    return True


def exercise_gui_write(pi: PiSession, service: str, run_dir: str, remote_dir: str) -> None:
    original = _gateway_get(pi, run_dir, service, "/Mode")
    target = 1 if _float_value(original) != 1.0 else 0
    mode_target = (service, "/Mode")
    _enqueue_core_control(pi, remote_dir, run_dir, target)
    _wait_for_gateway_value(pi, run_dir, mode_target, target, timeout=8.0)
    _enqueue_core_control(pi, remote_dir, run_dir, original)
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


def _enqueue_core_control(
    pi: PiSession,
    remote_dir: str,
    run_dir: str,
    value: object,
) -> None:
    code = (
        "import json;"
        "from venus_evcharger.dbus_gateway import gateway_paths;"
        "from venus_evcharger.ipc.core_commands import CoreCommandMailbox,core_control_command_payload;"
        f"mailbox=CoreCommandMailbox(gateway_paths({run_dir!r}).core_command_dir);"
        f"command=core_control_command_payload('set_mode','mode',{value!r},"
        "source='control-surface',origin='pi-release-gate');"
        "print(json.dumps({'ok':bool(mailbox.enqueue(command))}))"
    )
    raw = pi.ssh(f"cd {shlex.quote(remote_dir)} && python3 -c {shlex.quote(code)}", timeout=8.0)
    response = json_object(raw, detail="core control response")
    if response.get("ok") is not True:
        raise GateFailure(f"core mailbox rejected mode write: {response!r}")


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
