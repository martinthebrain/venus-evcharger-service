# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-side Shelly simulator helpers for the Raspberry-Pi release gate."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.request import urlopen

from pi_gateway_release_gate_common import GateFailure, ROOT


def route_local_ip(remote_host: str) -> str:
    host = remote_host.split("@", 1)[-1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 22))
        return str(sock.getsockname()[0])


def start_host_shelly(args: argparse.Namespace) -> subprocess.Popen[str] | None:
    if not args.start_host_shelly:
        return None
    process = subprocess.Popen(
        _shelly_command(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(0.5)
    if process.poll() is None:
        return process
    output = process.stdout.read() if process.stdout else ""
    raise GateFailure(f"mock Shelly simulator exited early:\n{output}")


def set_shelly_state(args: argparse.Namespace, *, energy_wh: float) -> None:
    _http_json(
        "http://127.0.0.1:"
        f"{int(args.shelly_port)}/__admin/state?relay=1&apower={float(args.shelly_power_w)}"
        f"&current={float(args.shelly_current_a)}&voltage={float(args.shelly_voltage_v)}"
        f"&total_energy_wh={float(energy_wh)}"
    )


def settle_with_shelly_energy(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + max(0.0, float(args.settle_seconds))
    started = time.monotonic()
    base_wh = float(args.shelly_energy_wh)
    while time.monotonic() < deadline:
        elapsed = max(0.0, time.monotonic() - started)
        set_shelly_state(args, energy_wh=base_wh + (float(args.shelly_power_w) * elapsed / 3600.0))
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))


def stop_simulator(simulator: subprocess.Popen[str] | None) -> None:
    if simulator is None:
        return
    simulator.terminate()
    try:
        simulator.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        simulator.kill()


def _shelly_command(args: argparse.Namespace) -> list[str]:
    simulator = ROOT / "scripts" / "dev" / "mock_shelly_rpc.py"
    return [
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


def _http_json(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - dev-only local testbed URL
        data = response.read().decode("utf-8")
    payload = json.loads(data)
    return payload if isinstance(payload, dict) else {}
