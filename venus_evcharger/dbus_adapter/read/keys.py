# SPDX-License-Identifier: GPL-3.0-or-later
"""Private cache keys used by the DBus adapter read scheduler."""

from __future__ import annotations

from typing import Literal

AdapterEnergyReadKey = Literal[
    "grid_power_w",
    "pv_power_w",
    "battery_soc",
    "battery_net_power_w",
]

GRID_POWER_READ_KEY: AdapterEnergyReadKey = "grid_power_w"
PV_POWER_READ_KEY: AdapterEnergyReadKey = "pv_power_w"
BATTERY_SOC_READ_KEY: AdapterEnergyReadKey = "battery_soc"
BATTERY_NET_POWER_READ_KEY: AdapterEnergyReadKey = "battery_net_power_w"
CORE_ENERGY_READ_KEYS: frozenset[AdapterEnergyReadKey] = frozenset(
    {
        GRID_POWER_READ_KEY,
        PV_POWER_READ_KEY,
        BATTERY_SOC_READ_KEY,
    }
)
