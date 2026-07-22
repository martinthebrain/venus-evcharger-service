#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition root for one generic Shelly configuration request."""

from __future__ import annotations

from collections.abc import Sequence

from venus_evcharger.dbus_gateway_client import (
    GatewayClient,
    GatewayGenericShellyConfigurationClient,
)
from venus_evcharger.ops.disable_generic_shelly_once import main as configuration_main


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pure configuration workflow with its gateway-backed port."""
    port = GatewayGenericShellyConfigurationClient(GatewayClient())
    return configuration_main(argv, configuration_port=port)


if __name__ == "__main__":
    raise SystemExit(main())
