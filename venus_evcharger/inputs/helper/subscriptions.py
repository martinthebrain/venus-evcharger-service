# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway subscription scheduling for the auto-input helper."""

from __future__ import annotations

import time
from collections.abc import Callable

from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.dbus_errors import DBUS_INPUT_READ_ERRORS
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings
from venus_evcharger.inputs.helper.contracts import (
    EnergyServiceResolverPort,
    EnergySourceCatalogPort,
    GatewayReaderPort,
    PvServiceResolverPort,
    SnapshotPort,
    SubscriptionSpec,
    WarningSink,
)
from venus_evcharger.inputs.helper.glib_runtime import GLIB_RUNTIME
SUBSCRIPTION_RESOLUTION_ERRORS: tuple[type[BaseException], ...] = (*DBUS_INPUT_READ_ERRORS, ValueError)
SUBSCRIPTION_CALLBACK_ERRORS: tuple[type[BaseException], ...] = (
    *SUBSCRIPTION_RESOLUTION_ERRORS,
    AssertionError,
    TypeError,
)


class SubscriptionManager:
    """Serialize gateway refresh requests and topology recovery."""

    def __init__(
        self,
        settings: AutoInputHelperSettings,
        gateway: GatewayReaderPort,
        pv_grid: PvServiceResolverPort,
        catalog: EnergySourceCatalogPort,
        resolver: EnergyServiceResolverPort,
        snapshots: SnapshotPort,
        warning: WarningSink,
        stop_requested: Callable[[], bool],
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.pv_grid = pv_grid
        self.catalog = catalog
        self.resolver = resolver
        self.snapshots = snapshots
        self.warning = warning
        self.stop_requested = stop_requested
        self._matches: set[SubscriptionSpec] = set()
        self._monitored: dict[SubscriptionSpec, dict[str, str]] = {}
        self._service_refresh_registered = False
        self._refresh_scheduled = False
        self._backoff_until = 0.0
        self._generation = 0

    def refresh(self) -> bool:
        if self._refresh_delay() > 0.0:
            self.schedule_refresh()
            return False
        try:
            self._register_service_refresh()
            desired = self.desired_specs()
            desired_keys = set(desired)
            for source_name, service_name, path in desired:
                self._subscribe(source_name, service_name, path)
            self._clear_missing(desired_keys)
            self.snapshots.refresh_all()
        except SUBSCRIPTION_CALLBACK_ERRORS as error:
            self._handle_error(
                "auto-helper-refresh-subscriptions",
                "Auto input helper failed to refresh DBus subscriptions: %s",
                error,
            )
        return False

    def schedule_refresh(self) -> None:
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        delay_seconds = self._refresh_delay()

        def run_refresh() -> bool:
            self._refresh_scheduled = False
            return False if self.stop_requested() else self.refresh()

        if delay_seconds > 0.0:
            GLIB_RUNTIME.timeout_add(max(1, int(delay_seconds * 1000)), run_refresh)
        else:
            GLIB_RUNTIME.idle_add(run_refresh)

    def timer_tick(self) -> bool:
        self.schedule_refresh()
        return not self.stop_requested()

    def source_changed(self, source_name: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return
        try:
            self.snapshots.refresh_source(source_name)
        except SUBSCRIPTION_CALLBACK_ERRORS as error:
            self._handle_error(
                f"auto-helper-source-signal-{source_name}",
                "Auto input helper failed to refresh %s after signal: %s",
                source_name,
                error,
            )

    def owner_changed(self, name: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return
        try:
            if self._relevant_owner(name):
                self.schedule_refresh()
        except SUBSCRIPTION_CALLBACK_ERRORS as error:
            self._handle_error(
                "auto-helper-name-owner-signal",
                "Auto input helper failed to process DBus owner change for %s: %s",
                name,
                error,
            )

    def desired_specs(self) -> list[SubscriptionSpec]:
        return self._pv_specs() + self._battery_specs() + self._grid_specs()

    def reset(self) -> None:
        self._clear_all()
        self._generation += 1

    def _pv_specs(self) -> list[SubscriptionSpec]:
        try:
            services = self._pv_services()
        except SUBSCRIPTION_RESOLUTION_ERRORS:
            services = []
        specs = [("pv", service, self.settings.auto_pv_path) for service in services]
        dc_spec = self._dc_pv_spec()
        if dc_spec is not None:
            specs.append(dc_spec)
        return specs

    def _pv_services(self) -> list[str]:
        if self.settings.auto_pv_service:
            return [self.settings.auto_pv_service]
        return self.pv_grid.resolve_pv_services()

    def _dc_pv_spec(self) -> SubscriptionSpec | None:
        if not self.settings.auto_use_dc_pv:
            return None
        if not self.settings.auto_dc_pv_service or not self.settings.auto_dc_pv_path:
            return None
        return ("pv", self.settings.auto_dc_pv_service, self.settings.auto_dc_pv_path)

    def _battery_specs(self) -> list[SubscriptionSpec]:
        sources = self.settings.auto_energy_sources or (self.catalog.primary_source(),)
        specs: list[SubscriptionSpec] = []
        for source in sources:
            specs.extend(self._battery_source_specs(source))
        return specs

    def _battery_source_specs(self, source: EnergySourceDefinition) -> list[SubscriptionSpec]:
        try:
            service_name = self.resolver.resolve(source)
        except SUBSCRIPTION_RESOLUTION_ERRORS:
            return []
        paths = (source.soc_path, source.battery_power_path, source.ac_power_path)
        return [("battery", service_name, path) for path in paths if path]

    def _grid_specs(self) -> list[SubscriptionSpec]:
        if not self.settings.auto_grid_service:
            return []
        paths = (
            self.settings.auto_grid_l1_path,
            self.settings.auto_grid_l2_path,
            self.settings.auto_grid_l3_path,
        )
        return [("grid", self.settings.auto_grid_service, path) for path in paths if path]

    def _subscribe(self, source_name: str, service_name: str, path: str) -> None:
        key = (source_name, service_name, path)
        if key in self._matches:
            return
        self._matches.add(key)
        self._monitored[key] = {"source": source_name, "service_name": service_name, "path": path}
        self.gateway.request_value(
            service_name,
            path,
            priority=80,
            reason=f"{source_name} subscription refresh",
        )

    def _clear_missing(self, desired: set[SubscriptionSpec]) -> None:
        stale = self._matches - desired
        self._matches.intersection_update(desired)
        for key in stale:
            self._monitored.pop(key, None)

    def _clear_all(self) -> None:
        self._matches.clear()
        self._monitored.clear()
        self._service_refresh_registered = False

    def _register_service_refresh(self) -> None:
        if not self._service_refresh_registered and self.gateway.request_service_refresh():
            self._service_refresh_registered = True

    def _refresh_delay(self) -> float:
        return max(0.0, self._backoff_until - time.time())

    def _handle_error(self, warning_key: str, message: str, *args: object) -> None:
        base = self.settings.auto_dbus_backoff_base_seconds or 5.0
        self.warning(warning_key, max(5.0, base), message, *args)
        self.reset()
        self._backoff_until = time.time() + max(1.0, base)
        self.schedule_refresh()

    def _relevant_owner(self, name: str) -> bool:
        if self._explicit_owner(name):
            return True
        return self._prefixed_owner(name)

    def _explicit_owner(self, name: str) -> bool:
        if name in {
            self.settings.auto_grid_service,
            self.settings.auto_dc_pv_service,
            self.settings.auto_pv_service,
            self.settings.auto_battery_service,
        }:
            return True
        if any(source.service_name == name for source in self.settings.auto_energy_sources):
            return True
        return False

    def _prefixed_owner(self, name: str) -> bool:
        return bool(
            name.startswith(self.settings.auto_pv_service_prefix)
            or name.startswith(self.settings.auto_battery_service_prefix)
            or any(
                source.service_prefix and name.startswith(source.service_prefix)
                for source in self.settings.auto_energy_sources
            )
        )
