#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a Raspberry-Pi release gate for the DBus gateway architecture.

This developer-only integration gate obeys the same isolation contract as the
runtime: it observes GUI values through the gateway cache and submits test
writes through the gateway socket.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pi_gateway_release_gate_support import (
    PiSession,
    prepare_gateway_release_test,
    print_release_gate_result,
    run_gateway_release_checks,
    stop_simulator,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run EV charger DBus gateway release checks on a Raspberry Pi.")
    parser.add_argument("--pi", default="root@192.168.142.129")
    parser.add_argument("--ssh-config", default="/dev/null")
    parser.add_argument("--remote-dir", default="/data/bootstrap-venus-evcharger/dbus-venus-evcharger")
    parser.add_argument("--gateway-run-dir", default="/run/venus-evcharger")
    parser.add_argument("--device-instance", type=int, default=60)
    parser.add_argument("--deploy", action="store_true", help="Copy the current workspace to --remote-dir before testing.")
    parser.add_argument("--restart", action="store_true", help="Restart Pi runit services before testing.")
    parser.add_argument("--configure-host", action="store_true", help="Write Host/DeviceInstance/RunDir into the Pi config.")
    parser.add_argument("--start-host-shelly", action="store_true", help="Run the Shelly simulator on this host.")
    parser.add_argument("--shelly-bind", default="0.0.0.0")
    parser.add_argument("--shelly-port", type=int, default=18080)
    parser.add_argument("--shelly-power-w", type=float, default=2000.0)
    parser.add_argument("--shelly-current-a", type=float, default=8.7)
    parser.add_argument("--shelly-voltage-v", type=float, default=230.0)
    parser.add_argument("--shelly-energy-wh", type=float, default=1234.0)
    parser.add_argument("--settle-seconds", type=float, default=20.0)
    parser.add_argument("--health-wait-seconds", type=float, default=90.0)
    parser.add_argument("--health-poll-seconds", type=float, default=5.0)
    parser.add_argument("--skip-chaos", action="store_true", help="Skip offline gateway chaos scenarios on the Pi.")
    parser.add_argument("--skip-gui-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    pi = PiSession(str(args.pi), ssh_config=str(args.ssh_config))
    service = f"com.victronenergy.evcharger.http_{int(args.device_instance)}"
    simulator = None
    try:
        simulator, _host_value = prepare_gateway_release_test(args, pi)
        health, values = run_gateway_release_checks(args, pi, service)
        print_release_gate_result(service, health, values)
        return 0
    finally:
        stop_simulator(simulator)


if __name__ == "__main__":
    raise SystemExit(main())
