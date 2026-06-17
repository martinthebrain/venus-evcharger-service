# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for the Raspberry-Pi DBus gateway release gate."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
GUI_PATHS = (
    "/Connected",
    "/Mode",
    "/StartStop",
    "/AutoStart",
    "/SetCurrent",
    "/Ac/Power",
    "/Ac/Current",
    "/Session/Energy",
    "/Session/Time",
    "/ChargingTime",
)


@dataclass(frozen=True)
class CommandResult:
    cmd: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


class GateFailure(RuntimeError):
    """Raised when one release-gate assertion fails."""


class PiSession:
    def __init__(self, target: str, *, ssh_config: str) -> None:
        self.target = target
        self.ssh_config = ssh_config

    def ssh(self, script: str, *, timeout: float = 30.0) -> str:
        result = _run(
            ["ssh", "-F", self.ssh_config, "-o", "BatchMode=yes", self.target, script],
            timeout=timeout,
        )
        return _require(result, f"ssh {self.target}")


def _run(cmd: Sequence[str], *, input_text: str | None = None, timeout: float = 30.0) -> CommandResult:
    completed = subprocess.run(
        list(cmd),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(tuple(cmd), completed.returncode, completed.stdout, completed.stderr)


def _require(result: CommandResult, detail: str) -> str:
    if result.returncode != 0:
        raise GateFailure(
            f"{detail} failed rc={result.returncode}\n"
            f"cmd={' '.join(result.cmd)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result.stdout.strip()


def _route_local_ip(remote_host: str) -> str:
    host = remote_host.split("@", 1)[-1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 22))
        return str(sock.getsockname()[0])


def _http_json(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - dev-only local testbed URL
        data = response.read().decode("utf-8")
    payload = json.loads(data)
    return payload if isinstance(payload, dict) else {}


def _set_shelly_state(args: argparse.Namespace, *, energy_wh: float) -> None:
    _http_json(
        "http://127.0.0.1:"
        f"{int(args.shelly_port)}/__admin/state?relay=1&apower={float(args.shelly_power_w)}"
        f"&current={float(args.shelly_current_a)}&voltage={float(args.shelly_voltage_v)}"
        f"&total_energy_wh={float(energy_wh)}"
    )


def _settle_with_shelly_energy(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + max(0.0, float(args.settle_seconds))
    start = time.monotonic()
    base_wh = float(args.shelly_energy_wh)
    while time.monotonic() < deadline:
        elapsed = max(0.0, time.monotonic() - start)
        energy_wh = base_wh + (float(args.shelly_power_w) * elapsed / 3600.0)
        _set_shelly_state(args, energy_wh=energy_wh)
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))


def _start_host_shelly(args: argparse.Namespace) -> subprocess.Popen[str] | None:
    if not args.start_host_shelly:
        return None
    simulator = ROOT / "scripts" / "dev" / "mock_shelly_rpc.py"
    cmd = [
        sys.executable,
        str(simulator),
        "--bind",
        str(args.shelly_bind),
        "--port",
        str(args.shelly_port),
        "--relay-on",
        "--apower",
        str(args.shelly_power_w),
        "--current",
        str(args.shelly_current_a),
        "--voltage",
        str(args.shelly_voltage_v),
        "--total-energy-wh",
        str(args.shelly_energy_wh),
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(0.5)
    if process.poll() is not None:
        output = process.stdout.read() if process.stdout else ""
        raise GateFailure(f"mock Shelly simulator exited early:\n{output}")
    return process


def _deploy_repo(pi: PiSession, remote_dir: str) -> None:
    excludes = (
        "--exclude=.git",
        "--exclude=.mypy_cache",
        "--exclude=.pytest_cache",
        "--exclude=.coverage",
        "--exclude=htmlcov",
        "--exclude=__pycache__",
        "--exclude=*.pyc",
    )
    pi.ssh(f"mkdir -p {remote_dir!r}", timeout=10.0)
    tar = subprocess.Popen(
        ["tar", *excludes, "-C", str(ROOT), "-czf", "-", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    ssh = subprocess.Popen(
        [
            "ssh",
            "-F",
            pi.ssh_config,
            "-o",
            "BatchMode=yes",
            pi.target,
            f"cd {remote_dir!r} && tar -xzf -",
        ],
        stdin=tar.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    if tar.stdout is not None:
        tar.stdout.close()
    ssh_stdout, ssh_stderr = ssh.communicate(timeout=90.0)
    tar_stderr = tar.stderr.read().decode("utf-8", errors="replace") if tar.stderr else ""
    tar_rc = tar.wait(timeout=10.0)
    if tar_rc != 0 or ssh.returncode != 0:
        raise GateFailure(
            "deploy failed\n"
            f"tar_rc={tar_rc} tar_stderr={tar_stderr}\n"
            f"ssh_rc={ssh.returncode} stdout={ssh_stdout.decode(errors='replace')}"
            f" stderr={ssh_stderr.decode(errors='replace')}"
        )


def _remote_compile(pi: PiSession, remote_dir: str) -> None:
    modules = " ".join(
        [
            "venus_evcharger_dbus_adapter.py",
            "venus_evcharger_service.py",
            "venus_evcharger_auto_input_helper.py",
            "venus_evcharger/dbus_gateway.py",
            "venus_evcharger/dbus_adapter_components.py",
            "venus_evcharger/dbus_adapter_read.py",
            "venus_evcharger/dbus_adapter_write.py",
            "scripts/dev/dbus_gateway_chaos.py",
        ]
    )
    pi.ssh(f"cd {remote_dir!r} && python3 -m py_compile {modules}", timeout=60.0)


def _remote_isolation(pi: PiSession, remote_dir: str) -> None:
    pi.ssh(f"cd {remote_dir!r} && python3 scripts/dev/check_dbus_isolation.py", timeout=60.0)


def _configure_remote(pi: PiSession, args: argparse.Namespace, host_value: str) -> None:
    if not host_value:
        return
    script = f"""
cd {args.remote_dir!r}
python3 - <<'PY'
from pathlib import Path
path = Path("deploy/venus/config.venus_evcharger.ini")
text = path.read_text()
updates = {{
    "Host": {host_value!r},
    "DeviceInstance": {str(args.device_instance)!r},
    "DbusGatewayRunDir": {args.gateway_run_dir!r},
}}
lines = []
seen = set()
for line in text.splitlines():
    key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else ""
    if key in updates:
        lines.append(f"{{key}}={{updates[key]}}")
        seen.add(key)
    else:
        lines.append(line)
for key, value in updates.items():
    if key not in seen:
        lines.append(f"{{key}}={{value}}")
path.write_text("\\n".join(lines) + "\\n")
PY
"""
    pi.ssh(script, timeout=20.0)


def _restart_remote_services(pi: PiSession) -> None:
    pi.ssh(
        "svc -d /service/dbus-venus-evcharger /service/dbus-venus-evcharger-dbus-adapter "
        "/service/dbus-venus-evcharger-observer 2>/dev/null || true; "
        "sleep 2; "
        "python3 - <<'PY'\n"
        "import os, signal, subprocess\n"
        "markers = (\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_service.py',\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_dbus_adapter.py',\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_observer.py',\n"
        "    '/data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_auto_input_helper.py',\n"
        ")\n"
        "protected = {os.getpid(), os.getppid()}\n"
        "out = subprocess.check_output(['ps', 'w'], text=True)\n"
        "for line in out.splitlines():\n"
        "    if not any(marker in line for marker in markers):\n"
        "        continue\n"
        "    fields = line.split(None, 1)\n"
        "    if not fields:\n"
        "        continue\n"
        "    try:\n"
        "        pid = int(fields[0])\n"
        "    except ValueError:\n"
        "        continue\n"
        "    if pid not in protected:\n"
        "        os.kill(pid, signal.SIGTERM)\n"
        "PY\n"
        "sleep 1; "
        "rm -rf /run/venus-evcharger/dbus-commands /run/venus-evcharger/core-commands; "
        "mkdir -p /run/venus-evcharger/dbus-commands /run/venus-evcharger/core-commands; "
        "svc -u /service/dbus-venus-evcharger-dbus-adapter /service/dbus-venus-evcharger "
        "/service/dbus-venus-evcharger-observer 2>/dev/null || true",
        timeout=30.0,
    )


def _assert_single_remote_instance(pi: PiSession) -> None:
    script = r"""
python3 - <<'PY'
import subprocess
patterns = {
    "service": "python3 /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_service.py",
    "adapter": "python3 /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_dbus_adapter.py",
    "observer": "python3 /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_observer.py",
}
out = subprocess.check_output(["ps", "w"], text=True)
for label, marker in patterns.items():
    count = sum(1 for line in out.splitlines() if marker in line and "grep" not in line)
    print(f"{label}={count}")
    if count != 1:
        raise SystemExit(10)
helper_marker = "python3 -u /data/bootstrap-venus-evcharger/dbus-venus-evcharger/venus_evcharger_auto_input_helper.py"
helpers = sum(1 for line in out.splitlines() if helper_marker in line and "grep" not in line)
print(f"helper={helpers}")
if helpers > 1:
    raise SystemExit(11)
PY
"""
    pi.ssh(script, timeout=15.0)


def _dbus_get(pi: PiSession, service: str, path: str) -> str:
    return pi.ssh(f"dbus -y {service!r} {path!r} GetValue", timeout=8.0).strip()


def _float_value(value: str) -> float:
    try:
        return float(value)
    except ValueError as error:
        raise GateFailure(f"not a numeric DBus value: {value!r}") from error


def _health(pi: PiSession, run_dir: str) -> dict[str, Any]:
    raw = pi.ssh(f"cat {run_dir.rstrip('/') + '/dbus-health.json'!r}", timeout=8.0)
    payload = json.loads(raw)
    health = payload.get("dbus_health")
    if not isinstance(health, dict):
        raise GateFailure("health.json has no dbus_health object")
    return health


def _health_failures(health: dict[str, Any]) -> list[str]:
    queues = health.get("queues", {}) if isinstance(health.get("queues"), dict) else {}
    eventloop = health.get("eventloop", {}) if isinstance(health.get("eventloop"), dict) else {}
    freshness = health.get("cache_freshness", {}) if isinstance(health.get("cache_freshness"), dict) else {}
    failures: list[str] = []
    failures.extend(_health_state_failures(health))
    failures.extend(_health_queue_failures(queues))
    failures.extend(_health_eventloop_failures(eventloop))
    failures.extend(_health_freshness_failures(freshness))
    return failures


def _health_state_failures(health: dict[str, Any]) -> list[str]:
    if str(health.get("state")) in {"ok", "degraded"}:
        return []
    return [f"unexpected gateway state {health.get('state')!r}"]


def _health_queue_failures(queues: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if float(queues.get("oldest_command_age_s", 0.0) or 0.0) > 30.0:
        failures.append(f"old command age {queues.get('oldest_command_age_s')}s")
    if int(queues.get("pending_command_count", 0) or 0) > 80:
        failures.append(f"too many pending commands {queues.get('pending_command_count')}")
    return failures


def _health_eventloop_failures(eventloop: dict[str, Any]) -> list[str]:
    if float(eventloop.get("max_tick_gap_ms_60s", 0.0) or 0.0) <= 1500.0:
        return []
    return [f"event-loop gap {eventloop.get('max_tick_gap_ms_60s')}ms"]


def _health_freshness_failures(freshness: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in ("grid_power_w", "pv_power_w"):
        failure = _health_freshness_failure(freshness, key)
        if failure:
            failures.append(failure)
    return failures


def _health_freshness_failure(freshness: dict[str, Any], key: str) -> str:
    status = freshness.get(f"{key}_status")
    age = float(freshness.get(f"{key}_age_s", 0.0) or 0.0)
    return f"{key} age {age}s" if status == "fresh" and age > 10.0 else ""


def _wait_for_healthy_gateway(pi: PiSession, run_dir: str, *, timeout: float, poll_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(0.1, float(timeout))
    last_health: dict[str, Any] = {}
    last_failures: list[str] = ["health was not checked"]
    while time.time() < deadline:
        last_health = _health(pi, run_dir)
        last_failures = _health_failures(last_health)
        if not last_failures:
            return last_health
        time.sleep(max(0.1, min(float(poll_seconds), deadline - time.time())))
    raise GateFailure("; ".join(last_failures) + f"\nlast_health={json.dumps(last_health, sort_keys=True)}")


def _assert_gui_values(pi: PiSession, service: str, *, expect_power: bool) -> dict[str, float]:
    values = {path: _dbus_get(pi, service, path) for path in GUI_PATHS}
    numeric = {path: _float_value(value) for path, value in values.items() if path not in {"/Mode"}}
    _assert_evcs_connected(values)
    if expect_power:
        _assert_power_values(numeric)
    return numeric


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


def prepare_gateway_release_test(args: argparse.Namespace, pi: PiSession) -> tuple[subprocess.Popen[str] | None, str]:
    simulator = _start_host_shelly(args)
    host_value = ""
    if args.start_host_shelly:
        host_value = f"{_route_local_ip(str(args.pi))}:{int(args.shelly_port)}"
        _set_shelly_state(args, energy_wh=float(args.shelly_energy_wh))
    if args.deploy:
        _deploy_repo(pi, str(args.remote_dir))
    if args.configure_host:
        _configure_remote(pi, args, host_value)
    return simulator, host_value


def run_gateway_release_checks(args: argparse.Namespace, pi: PiSession, service: str) -> tuple[dict[str, Any], dict[str, float]]:
    _remote_compile(pi, str(args.remote_dir))
    _remote_isolation(pi, str(args.remote_dir))
    if args.restart:
        _restart_remote_services(pi)
    _assert_single_remote_instance(pi)
    if args.start_host_shelly:
        _settle_with_shelly_energy(args)
    else:
        time.sleep(max(0.0, float(args.settle_seconds)))
    health = _wait_for_healthy_gateway(
        pi,
        str(args.gateway_run_dir),
        timeout=float(args.health_wait_seconds),
        poll_seconds=float(args.health_poll_seconds),
    )
    values = _assert_gui_values(pi, service, expect_power=True)
    if not args.skip_gui_write:
        _exercise_gui_write(pi, service)
    return health, values


def print_release_gate_result(service: str, health: dict[str, Any], values: dict[str, float]) -> None:
    print(
        json.dumps(
            {
                "ok": True,
                "service": service,
                "values": values,
                "health_state": health.get("state"),
                "queues": health.get("queues", {}),
                "eventloop": health.get("eventloop", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )


def stop_simulator(simulator: subprocess.Popen[str] | None) -> None:
    if simulator is None:
        return
    simulator.terminate()
    try:
        simulator.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        simulator.kill()


def _exercise_gui_write(pi: PiSession, service: str) -> None:
    original = _dbus_get(pi, service, "/Mode")
    target = "1" if str(original).strip() != "1" else "0"
    pi.ssh(f"dbus -y {service!r} /Mode SetValue {target}", timeout=8.0)
    _wait_for_dbus_value(pi, service, "/Mode", target, timeout=8.0)
    pi.ssh(f"dbus -y {service!r} /Mode SetValue {original}", timeout=8.0)
    _wait_for_dbus_value(pi, service, "/Mode", original, timeout=8.0)


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
