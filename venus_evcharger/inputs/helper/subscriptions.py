#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Venus EV charger service in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import logging
import time
from typing import Any

from gi.repository import GLib


class _GatewaySignalMatch:
    """Compatibility match object for gateway-managed cache refresh interests."""

    def remove(self) -> None:
        return


class _AutoInputHelperSubscriptionMixin:
    @staticmethod
    def _dbus_module() -> Any:
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    @staticmethod
    def _signal_spec_key(source_name: str, service_name: str, path: str) -> tuple[str, str, str]:
        """Return a stable key for one subscribed DBus path."""
        return (str(source_name), str(service_name), str(path))

    def _subscribe_busitem_path(self: Any, source_name: str, service_name: str, path: str) -> None:
        """Register interest in one source path without touching DBus directly."""
        self._ensure_poll_state()
        key = self._signal_spec_key(source_name, service_name, path)
        if key in self._signal_matches:
            return
        self._signal_matches[key] = _GatewaySignalMatch()
        self._monitored_specs[key] = {
            "source": source_name,
            "service_name": service_name,
            "path": path,
        }
        request_gateway_value = getattr(self, "_request_gateway_value", None)
        if callable(request_gateway_value):
            request_gateway_value(service_name, path, priority=80, reason=f"{source_name} subscription refresh")

    def _clear_missing_subscriptions(self: Any, desired_keys: set[tuple[str, str, str]]) -> None:
        """Remove subscriptions that are no longer needed."""
        self._ensure_poll_state()
        for key in list(self._signal_matches):
            if key in desired_keys:
                continue
            match = self._signal_matches.pop(key, None)
            self._remove_signal_match(match)
            self._monitored_specs.pop(key, None)

    def _desired_pv_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the current AC/DC PV paths that should be monitored."""
        pv_services = self._resolved_pv_subscription_services()
        desired = [("pv", service_name, self.auto_pv_path) for service_name in pv_services]
        dc_spec = self._dc_pv_subscription_spec()
        if dc_spec is not None:
            desired.append(dc_spec)
        return desired

    def _resolved_pv_subscription_services(self: Any) -> list[str]:
        """Return AC PV services that should currently be monitored."""
        try:
            return [self.auto_pv_service] if self.auto_pv_service else self._resolve_auto_pv_services()
        except Exception:  # pylint: disable=broad-except
            return []

    def _dc_pv_subscription_spec(self: Any) -> tuple[str, str, str] | None:
        """Return the optional DC PV subscription tuple when configured."""
        if self.auto_use_dc_pv and self.auto_dc_pv_service and self.auto_dc_pv_path:
            return ("pv", self.auto_dc_pv_service, self.auto_dc_pv_path)
        return None

    def _desired_battery_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the battery SOC path that should be monitored."""
        desired: list[tuple[str, str, str]] = []
        for source in tuple(getattr(self, "auto_energy_sources", ()) or (self._primary_energy_source(),)):
            desired.extend(self._battery_subscription_specs_for_source(source))
        return desired

    def _battery_subscription_specs_for_source(self: Any, source: Any) -> list[tuple[str, str, str]]:
        try:
            service_name = self._resolve_energy_source_service(source)
        except Exception:  # pylint: disable=broad-except
            return []
        return [
            ("battery", service_name, path)
            for path in self._battery_subscription_paths(source)
        ]

    @staticmethod
    def _battery_subscription_paths(source: Any) -> list[str]:
        return [
            path
            for path in (source.soc_path, source.battery_power_path, source.ac_power_path)
            if path
        ]

    def _desired_grid_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the configured grid power paths that should be monitored."""
        if not self.auto_grid_service:
            return []
        return [
            ("grid", self.auto_grid_service, path)
            for path in (self.auto_grid_l1_path, self.auto_grid_l2_path, self.auto_grid_l3_path)
            if path
        ]

    def _desired_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the currently desired DBus paths to monitor."""
        pv_specs: list[tuple[str, str, str]] = self._desired_pv_subscription_specs()
        battery_specs: list[tuple[str, str, str]] = self._desired_battery_subscription_specs()
        grid_specs: list[tuple[str, str, str]] = self._desired_grid_subscription_specs()
        return pv_specs + battery_specs + grid_specs

    def _refresh_subscriptions(self: Any) -> bool:
        """Rebuild path subscriptions after startup or a DBus service topology change."""
        self._ensure_poll_state()
        if self._subscription_refresh_backoff_active():
            self._schedule_refresh_subscriptions()
            return False
        try:
            self._register_name_owner_subscription()
            desired_specs = self._desired_subscription_specs()
            desired_keys = set()
            for source_name, service_name, path in desired_specs:
                key = self._signal_spec_key(source_name, service_name, path)
                desired_keys.add(key)
                self._subscribe_busitem_path(source_name, service_name, path)
            self._clear_missing_subscriptions(desired_keys)
            self._refresh_all_sources()
        except Exception as error:  # pylint: disable=broad-except
            self._handle_dbus_callback_error(
                "auto-helper-refresh-subscriptions",
                "Auto input helper failed to refresh DBus subscriptions: %s",
                error,
            )
        return False

    def _schedule_refresh_subscriptions(self: Any) -> None:
        """Schedule one deferred subscription rebuild."""
        self._ensure_poll_state()
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True
        delay_seconds = self._subscription_refresh_delay_seconds()

        def _run() -> bool:
            self._refresh_scheduled = False
            if self._stop_requested:
                return False
            refreshed: bool = self._refresh_subscriptions()
            return refreshed

        if delay_seconds > 0.0:
            GLib.timeout_add(max(1, int(delay_seconds * 1000)), _run)
        else:
            GLib.idle_add(_run)

    def _subscription_refresh_backoff_active(self: Any) -> bool:
        return bool(self._subscription_refresh_delay_seconds() > 0.0)

    def _subscription_refresh_delay_seconds(self: Any) -> float:
        backoff_until = float(getattr(self, "_dbus_subscription_backoff_until", 0.0) or 0.0)
        return max(0.0, backoff_until - time.time())

    def _on_source_signal(
        self: Any,
        source_name: str,
        dbus_generation: int | None = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Refresh one source when its DBus path emits a change signal."""
        del args, kwargs
        if not self._dbus_callback_generation_current(dbus_generation):
            return
        try:
            self._refresh_source(source_name)
        except Exception as error:  # pylint: disable=broad-except
            self._handle_dbus_callback_error(
                f"auto-helper-source-signal-{source_name}",
                "Auto input helper failed to refresh %s after signal: %s",
                source_name,
                error,
            )

    def _on_name_owner_changed(self: Any, *args: object) -> None:
        """Rebuild subscriptions when relevant DBus services appear or disappear."""
        dbus_generation, name = self._parse_name_owner_changed_args(args)
        if not self._dbus_callback_generation_current(dbus_generation):
            return
        try:
            if self._is_relevant_name_owner_change(name):
                self._schedule_refresh_subscriptions()
        except Exception as error:  # pylint: disable=broad-except
            self._handle_dbus_callback_error(
                "auto-helper-name-owner-signal",
                "Auto input helper failed to process DBus owner change for %s: %s",
                name,
                error,
            )

    @staticmethod
    def _parse_name_owner_changed_args(args: tuple[object, ...]) -> tuple[int | None, str]:
        if len(args) >= 4 and isinstance(args[0], int):
            return args[0], str(args[1])
        if args:
            return None, str(args[0])
        return None, ""

    def _dbus_callback_generation_current(self: Any, dbus_generation: int | None) -> bool:
        if dbus_generation is None:
            return True
        return int(dbus_generation) == int(getattr(self, "_dbus_generation", 0) or 0)

    def _handle_dbus_callback_error(self: Any, warning_key: str, message: str, *args: object) -> None:
        self._warning_throttled(
            warning_key,
            max(5.0, self.auto_dbus_backoff_base_seconds or 5.0),
            message,
            *args,
        )
        self._reset_system_bus()
        self._dbus_subscription_backoff_until = time.time() + max(1.0, self.auto_dbus_backoff_base_seconds or 5.0)
        self._schedule_refresh_subscriptions()

    def _is_relevant_name_owner_change(self: Any, name: str) -> bool:
        """Return whether a DBus owner-change affects monitored Auto-input services."""
        return bool(
            name == self.auto_grid_service
            or name == self.auto_dc_pv_service
            or self._matches_explicit_service_name(name)
            or self._matches_discovery_prefix(name)
        )

    def _matches_explicit_service_name(self: Any, name: str) -> bool:
        """Return whether one owner change matches explicit AC PV or battery services."""
        return bool(
            self._matches_explicit_pv_service_name(name)
            or self._matches_explicit_battery_service_name(name)
            or self._matches_explicit_energy_source_service_name(name)
        )

    def _matches_explicit_pv_service_name(self: Any, name: str) -> bool:
        return bool(self.auto_pv_service and name == self.auto_pv_service)

    def _matches_explicit_battery_service_name(self: Any, name: str) -> bool:
        return bool(self.auto_battery_service and name == self.auto_battery_service)

    def _matches_explicit_energy_source_service_name(self: Any, name: str) -> bool:
        return any(
            source.service_name and name == source.service_name
            for source in getattr(self, "auto_energy_sources", ())
        )

    def _matches_discovery_prefix(self: Any, name: str) -> bool:
        """Return whether one owner change matches auto-discovered PV/battery prefixes."""
        return bool(
            name.startswith(self.auto_pv_service_prefix)
            or name.startswith(self.auto_battery_service_prefix)
            or any(source.service_prefix and name.startswith(source.service_prefix) for source in getattr(self, "auto_energy_sources", ()))
        )

    def _refresh_subscriptions_timer(self: Any) -> bool:
        """Slow periodic refresh in case a DBus topology change signal was missed."""
        self._schedule_refresh_subscriptions()
        return not self._stop_requested

    def _parent_watchdog(self: Any) -> bool:
        """Stop the helper once the parent process disappears."""
        if self._stop_requested or self._parent_alive():
            return not self._stop_requested
        if self._main_loop is not None:
            self._main_loop.quit()
        return False

    def _reset_system_bus(self: Any) -> None:
        """Drop the cached DBus connection and all signal matches tied to it."""
        self._ensure_poll_state()
        bus = self._system_bus
        self._clear_all_signal_matches()
        self._close_system_bus(bus)
        self._system_bus = None
        self._system_bus_generation = 0
        self._dbus_generation = int(getattr(self, "_dbus_generation", 0) or 0) + 1

    def _get_system_bus(self: Any) -> Any:
        """Direct DBus connections are forbidden outside the gateway."""
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def _register_name_owner_subscription(self: Any) -> None:
        """Record that topology refresh is gateway-driven."""
        self._ensure_poll_state()
        if self._name_owner_match is not None:
            return
        gateway_client = getattr(self, "_gateway_client", None)
        if not callable(gateway_client):
            logging.debug("Gateway service refresh request skipped; gateway client is unavailable")
            return
        try:
            gateway_client().enqueue_command({"kind": "refresh_services", "priority": "normal"})
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Gateway service refresh request failed: %s", error)
            return
        self._name_owner_match = _GatewaySignalMatch()

    def _clear_all_signal_matches(self: Any) -> None:
        for key in list(getattr(self, "_signal_matches", {})):
            match = self._signal_matches.pop(key, None)
            self._remove_signal_match(match)
            self._monitored_specs.pop(key, None)
        self._remove_signal_match(getattr(self, "_name_owner_match", None))
        self._name_owner_match = None

    @staticmethod
    def _remove_signal_match(match: Any) -> None:
        if match is None:
            return
        try:
            match.remove()
        except Exception:  # pylint: disable=broad-except
            pass

    @staticmethod
    def _close_system_bus(bus: Any) -> None:
        if bus is None:
            return
        close = getattr(bus, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:  # pylint: disable=broad-except
            pass
