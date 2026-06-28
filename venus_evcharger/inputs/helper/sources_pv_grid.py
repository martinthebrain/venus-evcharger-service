#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PV and grid helper methods for the auto input helper."""

from __future__ import annotations

import logging
import time
from typing import Any

from venus_evcharger.core.shared import (
    configured_grid_paths,
    discovery_cache_valid,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
    grid_values_complete_enough,
)
from venus_evcharger.inputs.helper.sources_dbus_common import DBUS_SOURCE_READ_ERRORS


class _AutoInputHelperSourcePvGridMixin:
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
            sort_names=False,
        )
        self._resolved_auto_pv_services = resolved[: self.auto_pv_max_services]
        self._auto_pv_last_scan = now
        return list(self._resolved_auto_pv_services)

    def _resolved_pv_service_names(self: Any) -> tuple[list[str], bool]:
        try:
            service_names = self._resolve_auto_pv_services()
        except DBUS_SOURCE_READ_ERRORS as error:
            logging.debug("Auto helper AC PV service resolution failed: %s", error)
            return [], False
        return service_names, (not self.auto_pv_service and not service_names)

    def _read_ac_pv_total(self: Any, service_names: list[str]) -> tuple[float, bool]:
        total = 0.0
        seen_value = False
        for service_name in service_names:
            if self._dbus_introspection_says_skip(service_name, self.auto_pv_path):
                continue
            try:
                value = self._get_dbus_value(service_name, self.auto_pv_path)
            except DBUS_SOURCE_READ_ERRORS as error:
                logging.debug("Auto helper PV read failed for %s %s: %s", service_name, self.auto_pv_path, error)
                self._invalidate_auto_pv_services()
                continue
            numeric_value = sum_dbus_numeric(value)
            if numeric_value is None:
                continue
            total += numeric_value
            seen_value = True
        return total, seen_value

    def _read_dc_pv_power(self: Any) -> float | None:
        if not self.auto_use_dc_pv:
            return None
        try:
            dc_value = self._get_dbus_value(self.auto_dc_pv_service, self.auto_dc_pv_path)
        except DBUS_SOURCE_READ_ERRORS as error:
            logging.debug("Auto helper DC PV read failed for %s %s: %s", self.auto_dc_pv_service, self.auto_dc_pv_path, error)
            return None
        return sum_dbus_numeric(dc_value)

    def _get_pv_power(self: Any) -> float | None:
        if not self._source_retry_ready("pv"):
            return None
        service_names, no_auto_ac_services_found = self._resolved_pv_service_names()
        total, seen_value = self._read_ac_pv_total(service_names)
        numeric_dc_value = self._read_dc_pv_power()
        if numeric_dc_value is not None:
            total += numeric_dc_value
            seen_value = True
        if seen_value:
            return float(total)
        if should_assume_zero_pv(
            self.auto_pv_service,
            service_names,
            no_auto_ac_services_found,
            self.auto_use_dc_pv,
            numeric_dc_value,
        ):
            return 0.0
        self._delay_source_retry("pv")
        return None

    def _get_grid_power(self: Any) -> float | None:
        if not self._source_retry_ready("grid"):
            return None
        configured_paths = configured_grid_paths(self.auto_grid_l1_path, self.auto_grid_l2_path, self.auto_grid_l3_path)
        if not configured_paths:
            return None
        total, seen_value, missing_paths = self._grid_total_and_missing_paths(configured_paths)
        if grid_values_complete_enough(seen_value, missing_paths, self.auto_grid_require_all_phases):
            return float(total)
        self._delay_source_retry("grid")
        return None

    def _grid_total_and_missing_paths(self: Any, configured_paths: list[str]) -> tuple[float, bool, list[str]]:
        total = 0.0
        seen_value = False
        missing_paths: list[str] = []
        for path in configured_paths:
            numeric_value = self._grid_path_numeric_value(path)
            if numeric_value is None:
                missing_paths.append(path)
                continue
            total += numeric_value
            seen_value = True
        return total, seen_value, missing_paths

    def _grid_path_numeric_value(self: Any, path: str) -> float | None:
        try:
            value = self._get_dbus_value(self.auto_grid_service, path)
        except DBUS_SOURCE_READ_ERRORS:
            return None
        if value is None:
            return None
        return sum_dbus_numeric(value)
