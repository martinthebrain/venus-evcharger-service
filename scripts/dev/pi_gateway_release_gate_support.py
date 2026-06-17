# SPDX-License-Identifier: GPL-3.0-or-later
"""Orchestration helpers for the Raspberry-Pi DBus gateway release gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from typing import Any

from pi_gateway_release_gate_assertions import assert_gui_values, exercise_gui_write
from pi_gateway_release_gate_common import GateFailure, PiSession
from pi_gateway_release_gate_health import wait_for_healthy_gateway
from pi_gateway_release_gate_remote import (
    assert_single_remote_instance,
    configure_remote,
    deploy_repo,
    remote_compile,
    remote_isolation,
    restart_remote_services,
)
from pi_gateway_release_gate_shelly import (
    route_local_ip,
    set_shelly_state,
    settle_with_shelly_energy,
    start_host_shelly,
    stop_simulator,
)

__all__ = (
    "GateFailure",
    "PiSession",
    "prepare_gateway_release_test",
    "print_release_gate_result",
    "run_gateway_release_checks",
    "stop_simulator",
)


def prepare_gateway_release_test(args: argparse.Namespace, pi: PiSession) -> tuple[subprocess.Popen[str] | None, str]:
    simulator = start_host_shelly(args)
    host_value = ""
    if args.start_host_shelly:
        host_value = f"{route_local_ip(str(args.pi))}:{int(args.shelly_port)}"
        set_shelly_state(args, energy_wh=float(args.shelly_energy_wh))
    if args.deploy:
        deploy_repo(pi, str(args.remote_dir))
    if args.configure_host:
        configure_remote(pi, args, host_value)
    return simulator, host_value


def run_gateway_release_checks(args: argparse.Namespace, pi: PiSession, service: str) -> tuple[dict[str, Any], dict[str, float]]:
    remote_compile(pi, str(args.remote_dir))
    remote_isolation(pi, str(args.remote_dir))
    if args.restart:
        restart_remote_services(pi)
    assert_single_remote_instance(pi)
    _settle_runtime(args)
    health = wait_for_healthy_gateway(
        pi,
        str(args.gateway_run_dir),
        timeout=float(args.health_wait_seconds),
        poll_seconds=float(args.health_poll_seconds),
    )
    values = assert_gui_values(pi, service, expect_power=True)
    if not args.skip_gui_write:
        exercise_gui_write(pi, service)
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


def _settle_runtime(args: argparse.Namespace) -> None:
    if args.start_host_shelly:
        settle_with_shelly_energy(args)
        return
    time.sleep(max(0.0, float(args.settle_seconds)))
