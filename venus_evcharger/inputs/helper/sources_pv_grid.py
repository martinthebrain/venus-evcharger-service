# SPDX-License-Identifier: GPL-3.0-or-later
"""PV and grid source reads through semantic gateway keys."""

from __future__ import annotations

import time

from venus_evcharger.core.shared import discovery_cache_valid, prefixed_service_names
from venus_evcharger.dbus_gateway import GRID_POWER_READ_KEY, PV_POWER_READ_KEY
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import GatewayReaderPort


class PvGridSourceReader:
    def __init__(self, settings: AutoInputHelperSettings, gateway: GatewayReaderPort) -> None:
        self.settings = settings
        self.gateway = gateway
        self._resolved_pv_services: list[str] = []
        self._pv_scan_at = 0.0

    def resolve_pv_services(self) -> list[str]:
        if self.settings.auto_pv_service:
            return [self.settings.auto_pv_service]
        now = time.time()
        if discovery_cache_valid(
            self._resolved_pv_services,
            self._pv_scan_at,
            self.settings.auto_pv_scan_interval_seconds,
            now,
        ):
            return list(self._resolved_pv_services)
        self._resolved_pv_services = prefixed_service_names(
            self.gateway.service_names(),
            self.settings.auto_pv_service_prefix,
            max_services=self.settings.auto_pv_max_services,
        )
        self._pv_scan_at = now
        return list(self._resolved_pv_services)

    def invalidate_pv_services(self) -> None:
        self._resolved_pv_services = []
        self._pv_scan_at = 0.0

    def pv_power(self) -> float | None:
        if not self.gateway.source_retry_ready("pv"):
            return None
        value = self.gateway.semantic_value(PV_POWER_READ_KEY, reason="helper semantic PV power read")
        if value is not None:
            return float(value)
        self.gateway.delay_source_retry("pv")
        return None

    def grid_power(self) -> float | None:
        if not self.gateway.source_retry_ready("grid"):
            return None
        value = self.gateway.semantic_value(GRID_POWER_READ_KEY, reason="helper semantic grid power read")
        if value is not None:
            return float(value)
        self.gateway.delay_source_retry("grid")
        return None
