#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line entrypoint for the dedicated Venus EV charger DBus adapter."""

from __future__ import annotations

import argparse
import logging

from venus_evcharger.dbus_adapter.process.adapter import DbusAdapter
from venus_evcharger.dbus_adapter.process.config import (
    load_adapter_config,
    logging_level_from_config,
)
from venus_evcharger.ipc.gateway_path_config import configured_gateway_paths

__all__ = ["DbusAdapter", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Venus EV charger DBus adapter.")
    parser.add_argument("config_path", nargs="?", default="/data/etc/venus-evcharger-service/config.ini")
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args(argv)
    config = load_adapter_config(args.config_path)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d dbus-adapter] %(message)s",
        level=logging_level_from_config(config),
    )
    paths = configured_gateway_paths(
        config,
        run_dir_override=args.run_dir.strip() or None,
    )
    adapter = DbusAdapter(args.config_path, paths=paths)
    adapter.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - command line entrypoint
    raise SystemExit(main())
