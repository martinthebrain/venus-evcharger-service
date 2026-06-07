#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish a small simulated Victron battery service on Venus OS DBus."""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import time
from typing import Any

for _path in (
    "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    "/opt/victronenergy/dbus-modbus-client",
):
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

try:
    import dbus.mainloop.glib
    from gi.repository import GLib
    from vedbus import VeDbusService
except Exception as error:  # pragma: no cover - exercised on Venus OS
    print(f"Unable to import Venus DBus dependencies: {error}", file=sys.stderr)
    sys.exit(2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-name", default="com.victronenergy.battery.evcharger_sim")
    parser.add_argument("--device-instance", type=int, default=990)
    parser.add_argument("--soc", type=float, default=100.0)
    parser.add_argument("--installed-capacity-ah", type=float, default=100.0)
    parser.add_argument("--voltage", type=float, default=50.7)
    parser.add_argument("--power", type=float, default=0.0)
    parser.add_argument("--product-name", default="EV Charger Test Battery")
    parser.add_argument("--connection", default="EV charger DBus battery simulator")
    parser.add_argument("--set-after", default="", help="Optional seconds:soc:ah:voltage:power update.")
    return parser


def _add_common_paths(service: Any, args: argparse.Namespace) -> None:
    service.add_path("/Mgmt/ProcessName", os.path.abspath(__file__))
    service.add_path("/Mgmt/ProcessVersion", f"Python {platform.python_version()}")
    service.add_path("/Mgmt/Connection", args.connection)
    service.add_path("/DeviceInstance", int(args.device_instance))
    service.add_path("/ProductId", 0xBEEF)
    service.add_path("/ProductName", args.product_name)
    service.add_path("/CustomName", args.product_name)
    service.add_path("/FirmwareVersion", "simulator")
    service.add_path("/HardwareVersion", "simulator")
    service.add_path("/Serial", "evcharger-test-battery")
    service.add_path("/Connected", 1)
    service.add_path("/UpdateIndex", 0)


def _add_battery_paths(service: Any, args: argparse.Namespace) -> None:
    service.add_path("/Soc", float(args.soc))
    service.add_path("/InstalledCapacity", float(args.installed_capacity_ah))
    service.add_path("/Dc/0/Voltage", float(args.voltage))
    service.add_path("/Dc/0/Power", float(args.power))


def _apply_update(service: Any, update_spec: str) -> bool:
    parts = [part.strip() for part in update_spec.split(":")]
    if len(parts) != 5:
        return False
    _, soc, installed_capacity_ah, voltage, power = parts
    service["/Soc"] = float(soc)
    service["/InstalledCapacity"] = float(installed_capacity_ah)
    service["/Dc/0/Voltage"] = float(voltage)
    service["/Dc/0/Power"] = float(power)
    service["/UpdateIndex"] = int(service["/UpdateIndex"]) + 1
    print(
        "Updated battery simulator: "
        f"soc={soc} ah={installed_capacity_ah} voltage={voltage} power={power}",
        flush=True,
    )
    return False


def _schedule_update(loop: Any, service: Any, update_spec: str) -> None:
    if not update_spec:
        return
    try:
        delay_seconds = max(0.0, float(update_spec.split(":", 1)[0]))
    except (TypeError, ValueError):
        print(f"Ignoring invalid --set-after value: {update_spec}", file=sys.stderr)
        return
    GLib.timeout_add(int(delay_seconds * 1000), _apply_update, service, update_spec)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    service = VeDbusService(args.service_name, register=False)
    _add_common_paths(service, args)
    _add_battery_paths(service, args)
    service.register()
    loop = GLib.MainLoop()
    _schedule_update(loop, service, args.set_after)

    def _stop(_signum: int, _frame: object) -> None:
        loop.quit()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(
        f"Battery simulator {args.service_name} running "
        f"soc={args.soc} ah={args.installed_capacity_ah} voltage={args.voltage}",
        flush=True,
    )
    loop.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entrypoint
    raise SystemExit(main())
