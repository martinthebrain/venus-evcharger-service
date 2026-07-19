# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI freshness path groups for adapter health reporting."""

from __future__ import annotations

from venus_evcharger.dbus_gateway import GUI_CRITICAL_PUBLISH_PATHS

GUI_MEASUREMENT_FRESHNESS_PATHS = {
    "/Ac/Power",
    "/Ac/Current",
    "/Current",
    "/Ac/L1/Power",
    "/Ac/L1/Current",
    "/Ac/L2/Power",
    "/Ac/L2/Current",
    "/Ac/L3/Power",
    "/Ac/L3/Current",
}
ACTIVE_SESSION_GUI_FRESHNESS_PATHS = {
    "/Ac/Energy/Forward",
    "/Session/Time",
    "/Session/Energy",
    "/ChargingTime",
}
GUI_CONTROL_FRESHNESS_PATHS = (
    GUI_CRITICAL_PUBLISH_PATHS - GUI_MEASUREMENT_FRESHNESS_PATHS - ACTIVE_SESSION_GUI_FRESHNESS_PATHS
)
