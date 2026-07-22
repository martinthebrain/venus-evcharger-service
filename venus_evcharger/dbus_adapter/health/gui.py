# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic EVCS field groups used by gateway health reporting."""

from __future__ import annotations

GUI_MEASUREMENT_FRESHNESS_FIELDS: frozenset[str] = frozenset(
    (
        "ac_power_w",
        "ac_current_a",
        "charge_current_a",
        "l1_power_w",
        "l1_current_a",
        "l2_power_w",
        "l2_current_a",
        "l3_power_w",
        "l3_current_a",
    )
)
ACTIVE_SESSION_GUI_FRESHNESS_FIELDS: frozenset[str] = frozenset(
    (
        "energy_forward_kwh",
        "session_time_s",
        "session_energy_kwh",
        "charging_time_s",
    )
)
GUI_CONTROL_FRESHNESS_FIELDS: frozenset[str] = frozenset(
    (
        "connected",
        "mode",
        "start_stop",
        "enable",
        "auto_start",
        "status",
        "set_current",
    )
)
