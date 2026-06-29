# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus and energy-source resolution helpers for the auto input helper."""

from __future__ import annotations

from venus_evcharger.inputs.helper.sources_dbus_common import (
    _dbus_error_name,
    _is_expected_missing_dbus_error,
)
from venus_evcharger.inputs.helper.capacity_persistence import persist_estimated_capacity_if_ah_changed
from venus_evcharger.inputs.helper.sources_dbus_gateway import _AutoInputHelperSourceDbusGateway
from venus_evcharger.inputs.helper.sources_dbus_primary import _AutoInputHelperSourceDbusPrimary
from venus_evcharger.inputs.helper.sources_dbus_resolve import _AutoInputHelperSourceDbusResolve
from venus_evcharger.inputs.helper.sources_dbus_snapshot import _AutoInputHelperSourceDbusSnapshot


class _AutoInputHelperSourceDbus(
    _AutoInputHelperSourceDbusSnapshot,
    _AutoInputHelperSourceDbusResolve,
    _AutoInputHelperSourceDbusPrimary,
    _AutoInputHelperSourceDbusGateway,
):
    """Compose DBus source roles used by the auto-input helper."""


__all__ = (
    "_AutoInputHelperSourceDbus",
    "_dbus_error_name",
    "_is_expected_missing_dbus_error",
    "persist_estimated_capacity_if_ah_changed",
)
