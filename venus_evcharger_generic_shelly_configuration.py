#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition root for one generic Shelly configuration request."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from venus_evcharger.dbus_gateway_client import (
    GatewayClient,
    GatewayGenericShellyConfigurationClient,
)
from venus_evcharger.ipc.gateway_path_config import load_configured_gateway_paths
from venus_evcharger.ops.disable_generic_shelly_once import (
    DEFAULT_CONFIG_PATH,
    GENERIC_SHELLY_HELPER_ERRORS,
    main as configuration_main,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pure configuration workflow with its gateway-backed port."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    config_path = arguments[0] if arguments else DEFAULT_CONFIG_PATH
    try:
        paths = load_configured_gateway_paths(config_path)
    except GENERIC_SHELLY_HELPER_ERRORS as error:
        logging.error("Unable to load gateway path configuration: %s", error)
        return 1
    port = GatewayGenericShellyConfigurationClient(GatewayClient(paths))
    return int(configuration_main(arguments, configuration_port=port))


if __name__ == "__main__":  # pragma: no cover - command line entrypoint
    raise SystemExit(main())
