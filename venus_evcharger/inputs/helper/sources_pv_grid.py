#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PV and grid helper methods for the auto input helper."""

from __future__ import annotations

import time
from typing import Any

from venus_evcharger.core.shared import (
    discovery_cache_valid,
    prefixed_service_names,
)
from venus_evcharger.dbus_gateway import GRID_POWER_READ_KEY, PV_POWER_READ_KEY
from venus_evcharger.inputs.helper.sources_dbus import _AutoInputHelperSourceDbus


class _AutoInputHelperSourcePvGrid(_AutoInputHelperSourceDbus):
    def _invalidate_auto_pv_services(self: Any) -> None:
        setattr(self, "_resolved_auto_pv_services", [])
        self._auto_pv_last_scan = 0.0

    def _resolve_auto_pv_services(self: Any) -> list[str]:
        if self.auto_pv_service:
            return [self.auto_pv_service]
        now = time.time()
        if discovery_cache_valid(
            self._resolved_auto_pv_services,
            self._auto_pv_last_scan,
            self.auto_pv_scan_interval_seconds,
            now,
        ):
            return list(self._resolved_auto_pv_services)
        resolved = prefixed_service_names(
        self._list_dbus_services(),
        self.auto_pv_service_prefix,
        max_services=self.auto_pv_max_services,
    )
        self._resolved_auto_pv_services = resolved
        self._auto_pv_last_scan = now
        return list(self._resolved_auto_pv_services)

    def _get_pv_power(self: Any) -> float | None:
        if not self._source_retry_ready("pv"):
            return None
        value = self._get_gateway_read_value(PV_POWER_READ_KEY, reason="helper semantic PV power read")
        if value is not None:
            return float(value)
        self._delay_source_retry("pv")
        return None

    def _get_grid_power(self: Any) -> float | None:
        if not self._source_retry_ready("grid"):
            return None
        value = self._get_gateway_read_value(GRID_POWER_READ_KEY, reason="helper semantic grid power read")
        if value is not None:
            return float(value)
        self._delay_source_retry("grid")
        return None
