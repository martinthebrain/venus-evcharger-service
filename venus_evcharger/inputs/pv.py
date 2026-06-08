# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, cast

import dbus
from venus_evcharger.core.shared import (
    coerce_dbus_numeric,
    discovery_cache_valid,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
)
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from venus_evcharger.dbus_introspection import owner_path_unusable, request_owner_introspection


class _DbusInputPvMixin(_ComposableControllerMixin):
    def _dbus_module(self) -> Any:
        """Prefer the DBus module from the service entrypoint for legacy test patches."""
        host = getattr(self.service, "_service", self.service)
        module_name = getattr(type(host), "__module__", "")
        service_module = sys.modules.get(module_name)
        service_dbus = getattr(service_module, "dbus", None) if service_module is not None else None
        if service_dbus is not None:
            return service_dbus
        return cast(Any, dbus)

    @staticmethod
    def _coerce_dbus_value(value: Any) -> float | int | None:
        """Convert raw DBus values to numbers where possible."""
        coerced_value = coerce_dbus_numeric(value)
        return cast(float | int | None, coerced_value)

    @staticmethod
    def _numeric_sum(value: Any) -> float | None:
        """Return a usable numeric sum for scalar or sequence power values."""
        numeric_sum = sum_dbus_numeric(value)
        return numeric_sum

    @staticmethod
    def _mark_dbus_success(svc: Any) -> None:
        """Record a successful DBus read or listing operation."""
        svc._last_dbus_ok_at = time.time()
        svc._mark_recovery("dbus", "DBus reads recovered")

    def _source_retry_ready(self, source_key: str, now: float) -> bool:
        """Return whether a logical input source may currently be queried."""
        return cast(bool, self.service._source_retry_ready(source_key, now))

    def _mark_source_recovery(self, source_key: str, message: str, *args: Any) -> None:
        """Record that one logical input source has recovered."""
        self.service._mark_recovery(source_key, message, *args)

    def _handle_source_failure(
        self,
        source_key: str,
        now: float,
        warning_key: str,
        warning_interval: float,
        warning_message: str,
        *args: Any,
    ) -> float | None:
        """Apply the standard failure/backoff/warning flow for one input source."""
        svc = self.service
        svc._mark_failure(source_key)
        svc._delay_source_retry(source_key, now)
        svc._warning_throttled(warning_key, warning_interval, warning_message, *args)
        return None

    def get_dbus_value(self, service_name: str, path: str) -> float | int | None:
        """Read a DBus value via com.victronenergy.BusItem."""
        svc = self.service
        skip, reason = owner_path_unusable(svc, service_name, path)
        if skip:
            logging.debug("Skipping %s %s from DBus introspection cache: %s", service_name, path, reason)
            request_owner_introspection(
                svc,
                service_name,
                path,
                priority=90,
                reason="main input read skipped known-unusable path",
                source="evcharger-inputs",
            )
            return None
        last_error: Exception | None = None
        timeout = getattr(svc, "dbus_method_timeout_seconds", 1.0)
        dbus_module = self._dbus_module()
        for attempt in range(2):
            try:
                bus = svc._get_system_bus()
                obj = bus.get_object(service_name, path, introspect=False)
                interface = dbus_module.Interface(obj, "com.victronenergy.BusItem")
                value = interface.GetValue(timeout=timeout)
                self._mark_dbus_success(svc)
                return self._coerce_dbus_value(value)
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                svc._reset_system_bus()
                if attempt == 0:
                    svc._mark_failure("dbus")
                    logging.debug(
                        "DBus read retry for %s %s after error: %s",
                        service_name,
                        path,
                        error,
                    )
        assert last_error is not None
        request_owner_introspection(
            svc,
            service_name,
            path,
            priority=100,
            reason="main input DBus read failed",
            source="evcharger-inputs",
        )
        raise last_error

    def list_dbus_services(self) -> list[str]:
        """List DBus services with exponential backoff for unstable buses."""
        svc = self.service
        self._ensure_dbus_list_state()
        now = time.time()
        if now < svc._dbus_list_backoff_until:
            raise RuntimeError("DBus list backoff active")
        try:
            names = self._list_dbus_names()
            svc._dbus_list_failures = 0
            svc._dbus_list_backoff_until = 0.0
            self._mark_dbus_success(svc)
            return names
        except Exception:  # pylint: disable=broad-except
            svc._reset_system_bus()
            svc._dbus_list_failures += 1
            svc._mark_failure("dbus")
            delay = self._dbus_list_backoff_delay()
            svc._dbus_list_backoff_until = now + delay
            raise

    def _ensure_dbus_list_state(self) -> None:
        """Populate DBus-list retry/backoff defaults used by list_dbus_services()."""
        svc = self.service
        self._ensure_service_attr(svc, "_dbus_list_backoff_until", 0.0)
        self._ensure_service_attr(svc, "_dbus_list_failures", 0)
        self._ensure_service_attr(svc, "auto_dbus_backoff_base_seconds", 5.0)
        self._ensure_service_attr(svc, "auto_dbus_backoff_max_seconds", 60.0)
        self._ensure_service_attr(svc, "dbus_method_timeout_seconds", 1.0)

    @staticmethod
    def _ensure_service_attr(svc: Any, attr_name: str, default: object) -> None:
        """Populate one service attribute when it is missing."""
        if not hasattr(svc, attr_name):
            setattr(svc, attr_name, default)

    def _list_dbus_names(self) -> list[str]:
        """Return the raw DBus name list through the freedesktop DBus interface."""
        svc = self.service
        bus = svc._get_system_bus()
        dbus_obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus", introspect=False)
        dbus_module = self._dbus_module()
        dbus_if = dbus_module.Interface(dbus_obj, "org.freedesktop.DBus")
        return [str(name) for name in dbus_if.ListNames(timeout=svc.dbus_method_timeout_seconds)]

    def _dbus_list_backoff_delay(self) -> float:
        """Return the current exponential-backoff delay for DBus name listing."""
        svc = self.service
        return float(min(
            svc.auto_dbus_backoff_max_seconds,
            svc.auto_dbus_backoff_base_seconds * (2 ** (svc._dbus_list_failures - 1)),
        ))

    def invalidate_auto_pv_services(self) -> None:
        """Clear cached PV service discovery so the next read performs a fresh scan."""
        svc = self.service
        svc._resolved_auto_pv_services = []
        svc._auto_pv_last_scan = 0.0

    def invalidate_auto_battery_service(self) -> None:
        """Clear cached battery service discovery so the next read performs a fresh scan."""
        svc = self.service
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0

    def resolve_auto_pv_services(self) -> list[str]:
        """Resolve AC PV services (or use explicit override) for Auto mode."""
        svc = self.service
        if svc.auto_pv_service:
            return [svc.auto_pv_service]

        now = time.time()
        if discovery_cache_valid(
            svc._resolved_auto_pv_services,
            svc._auto_pv_last_scan,
            svc.auto_pv_scan_interval_seconds,
            now,
        ):
            return cast(list[str], svc._resolved_auto_pv_services)

        service_names = prefixed_service_names(
            svc._list_dbus_services(),
            svc.auto_pv_service_prefix,
            max_services=svc.auto_pv_max_services,
            sort_names=True,
        )
        svc._resolved_auto_pv_services = service_names
        svc._auto_pv_last_scan = now
        logging.debug("Auto PV services resolved: %s", svc._resolved_auto_pv_services)
        if not svc._resolved_auto_pv_services:
            raise ValueError(f"No DBus service found with prefix '{svc.auto_pv_service_prefix}'")
        resolved_services: list[str] = svc._resolved_auto_pv_services
        return resolved_services

    def _resolve_pv_service_names(self) -> tuple[list[str], bool]:
        """Resolve current AC PV service names and whether auto-discovery found none."""
        svc = self.service
        try:
            return svc._resolve_auto_pv_services(), False
        except ValueError as error:
            logging.debug("Auto AC PV service resolution found no services: %s", error)
            return [], not bool(svc.auto_pv_service)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto AC PV service resolution failed: %s", error)
            return [], False

    @staticmethod
    def _should_rescan_pv_services(
        svc: Any,
        service_names: list[str],
        seen_value: bool,
        saw_ac_service_failure: bool,
    ) -> bool:
        """Return whether a one-time rescan of cached AC PV services is worthwhile."""
        return (
            saw_ac_service_failure
            and not seen_value
            and not svc.auto_pv_service
            and bool(service_names)
        )

    def _read_pv_service_values(
        self,
        service_names: list[str],
        log_suffix: str = "",
    ) -> tuple[float, bool, bool]:
        """Read AC PV values from the provided services."""
        svc = self.service
        total = 0.0
        seen_value = False
        saw_failure = False
        for service_name in service_names:
            try:
                value = svc._get_dbus_value(service_name, svc.auto_pv_path)
            except Exception as error:  # pylint: disable=broad-except
                logging.debug(
                    "Auto PV read failed%s for %s %s: %s",
                    log_suffix,
                    service_name,
                    svc.auto_pv_path,
                    error,
                )
                saw_failure = True
                continue
            if value is not None:
                numeric_value = self._numeric_sum(value)
                if numeric_value is not None:
                    total += numeric_value
                    seen_value = True
        return total, seen_value, saw_failure

    def _read_rescanned_pv_services(self) -> tuple[float, bool]:
        """Refresh cached AC PV services once and retry reading them."""
        svc = self.service
        svc._invalidate_auto_pv_services()
        try:
            rescanned_services = svc._resolve_auto_pv_services()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto AC PV rescan failed: %s", error)
            return 0.0, False
        total, seen_value, _ = self._read_pv_service_values(
            rescanned_services,
            log_suffix=" after rescan",
        )
        return total, seen_value

    def _read_dc_pv_value(self) -> tuple[Any, bool]:
        """Read optional DC PV power."""
        svc = self.service
        if not svc.auto_use_dc_pv:
            return None, False
        try:
            return svc._get_dbus_value(svc.auto_dc_pv_service, svc.auto_dc_pv_path), False
        except Exception as error:  # pylint: disable=broad-except
            logging.debug(
                "Auto DC PV read failed for %s %s: %s",
                svc.auto_dc_pv_service,
                svc.auto_dc_pv_path,
                error,
            )
            return None, True

    def _should_assume_zero_pv(
        self,
        service_names: list[str],
        no_auto_ac_services_found: bool,
        dc_value: Any,
    ) -> bool:
        """Return whether missing PV data should be treated as 0 W."""
        svc = self.service
        assume_zero = should_assume_zero_pv(
            svc.auto_pv_service,
            service_names,
            no_auto_ac_services_found,
            svc.auto_use_dc_pv,
            dc_value,
        )
        return assume_zero

    def _handle_missing_pv_power(
        self,
        service_names: list[str],
        no_auto_ac_services_found: bool,
        dc_value: Any,
        dc_read_failed: bool,
        now: float,
    ) -> float | None:
        """Handle the fallback when neither AC nor DC PV yielded a usable value."""
        svc = self.service
        if self._should_assume_zero_pv(service_names, no_auto_ac_services_found, dc_value):
            logging.debug("No readable AC/DC PV values discovered; assuming 0 W PV power.")
            svc._last_pv_missing_warning = None
            if not dc_read_failed:
                self._mark_source_recovery("pv", "No readable PV values discovered, assuming 0 W")
            return 0.0

        return self._handle_source_failure(
            "pv",
            now,
            "pv-missing",
            svc.auto_pv_scan_interval_seconds,
            "Auto mode could not read any PV power values from %s (or DC PV).",
            svc.auto_pv_service or svc.auto_pv_service_prefix,
        )

    def get_pv_power(self) -> float | None:
        """Return summed PV power from all AC PV services and optional DC PV."""
        svc = self.service
        now = time.time()
        if not self._source_retry_ready("pv", now):
            return None
        service_names, no_auto_ac_services_found = self._resolve_pv_service_names()
        total, seen_value = self._ac_pv_total_with_optional_rescan(service_names)
        total, dc_value, dc_read_failed, seen_value = self._dc_pv_total(total, seen_value)

        if seen_value:
            svc._last_pv_missing_warning = None
            self._mark_source_recovery("pv", "PV readings recovered")
            return total
        return self._handle_missing_pv_power(
            service_names,
            no_auto_ac_services_found,
            dc_value,
            dc_read_failed,
            now,
        )

    def _ac_pv_total_with_optional_rescan(self, service_names: list[str]) -> tuple[float, bool]:
        """Return AC PV total, retrying once with a refreshed service scan when useful."""
        svc = self.service
        total, seen_value, saw_ac_service_failure = self._read_pv_service_values(service_names)
        if not self._should_rescan_pv_services(svc, service_names, seen_value, saw_ac_service_failure):
            return total, seen_value
        rescanned_total, rescanned_seen_value = self._read_rescanned_pv_services()
        return total + rescanned_total, bool(seen_value or rescanned_seen_value)

    def _dc_pv_total(self, total: float, seen_value: bool) -> tuple[float, Any, bool, bool]:
        """Return updated totals plus raw DC payload and failure flags."""
        dc_value, dc_read_failed = self._read_dc_pv_value()
        numeric_dc_value = self._numeric_sum(dc_value)
        if numeric_dc_value is None:
            return total, dc_value, dc_read_failed, seen_value
        return total + numeric_dc_value, dc_value, dc_read_failed, True
