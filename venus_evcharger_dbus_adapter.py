#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line entrypoint for the dedicated Venus EV charger DBus adapter."""

from __future__ import annotations

import argparse
import configparser
import logging

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter, _logging_level_from_config
from venus_evcharger.dbus_gateway import gateway_paths

__all__ = ["DbusAdapter", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Venus EV charger DBus adapter.")
    parser.add_argument("config_path", nargs="?", default="/data/etc/venus-evcharger-service/config.ini")
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args(argv)
    config = configparser.ConfigParser()
    config.read(args.config_path)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d dbus-adapter] %(message)s",
        level=_logging_level_from_config(config),
    )
    adapter = DbusAdapter(args.config_path, paths=gateway_paths(args.run_dir or None))
    adapter.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entrypoint
    raise SystemExit(main())
