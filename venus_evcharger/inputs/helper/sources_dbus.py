# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus and energy-source resolution helpers for the auto input helper."""

from __future__ import annotations

from venus_evcharger.inputs.helper.sources_dbus_common import (
    _dbus_error_name,
    _is_expected_missing_dbus_error,
)
from venus_evcharger.inputs.helper.capacity_persistence import persist_estimated_capacity_if_ah_changed
from venus_evcharger.inputs.helper.sources_dbus_gateway import _AutoInputHelperSourceDbusGatewayMixin
from venus_evcharger.inputs.helper.sources_dbus_primary import _AutoInputHelperSourceDbusPrimaryMixin
from venus_evcharger.inputs.helper.sources_dbus_resolve import _AutoInputHelperSourceDbusResolveMixin
from venus_evcharger.inputs.helper.sources_dbus_snapshot import _AutoInputHelperSourceDbusSnapshotMixin


class _AutoInputHelperSourceDbusMixin(
    _AutoInputHelperSourceDbusSnapshotMixin,
    _AutoInputHelperSourceDbusResolveMixin,
    _AutoInputHelperSourceDbusPrimaryMixin,
    _AutoInputHelperSourceDbusGatewayMixin,
):
    """Compose DBus source helpers while keeping the public mixin import stable."""


__all__ = (
    "_AutoInputHelperSourceDbusMixin",
    "_dbus_error_name",
    "_is_expected_missing_dbus_error",
    "persist_estimated_capacity_if_ah_changed",
)
