# SPDX-License-Identifier: GPL-3.0-or-later
"""Config-value publishing helpers for DBus publishing."""

from __future__ import annotations

import time

from venus_evcharger.backend.models import effective_supported_phase_selections
from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.publish.dbus_ports import ConfigLearnedPort, ConfigRuntimeViewPort, FieldPublisherPort
from venus_evcharger.publish.dbus_shared import (
    DbusPublishContext,
    PublishServicePort,
    PublishValue,
    diagnostic_text,
)


class DbusPublishConfig:
    """Build and publish the EVCS configuration and control snapshot."""

    def __init__(
        self,
        context: DbusPublishContext,
        core: FieldPublisherPort,
        learned: ConfigLearnedPort,
        runtime_view: ConfigRuntimeViewPort,
    ) -> None:
        self.service: PublishServicePort = context.service
        self.core = core
        self.learned = learned
        self.runtime_view = runtime_view

    def _config_values(self, startstop_display: int, now: float | None) -> dict[str, PublishValue]:
        """Return mode and control values keyed by semantic EVCS field."""
        charger_enabled = self.learned.charger_enabled_readback(now)
        current_time = time.time() if now is None else float(now)
        effective_supported = effective_supported_phase_selections(
            self.runtime_view.configured_supported_phase_selections(self.service),
            lockout_selection=getattr(self.service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(self.service, "_phase_switch_lockout_until", None),
            now=current_time,
        )
        enable_display = (
            int(bool(charger_enabled))
            if charger_enabled is not None
            else int(getattr(self.service, "virtual_enable", 1))
        )
        startstop_value = int(bool(charger_enabled)) if charger_enabled is not None else int(startstop_display)
        return {
            "connected": self._connected_display(now),
            "status": int(getattr(self.service, "last_status", 0)),
            "mode": int(getattr(self.service, "virtual_mode", 0)),
            "auto_start": int(getattr(self.service, "virtual_autostart", 1)),
            "start_stop": startstop_value,
            "enable": enable_display,
            "phase_selection": str(getattr(self.service, "requested_phase_selection", "P1")),
            "phase_selection_active": str(getattr(self.service, "active_phase_selection", "P1")),
            "supported_phase_selections": ",".join(effective_supported),
            "set_current": self.learned.display_set_current(now),
            "min_current": getattr(self.service, "min_current", 0.0),
            "max_current": getattr(self.service, "max_current", 0.0),
            "auto_start_surplus_watts": getattr(self.service, "auto_start_surplus_watts", 0.0),
            "auto_stop_surplus_watts": getattr(self.service, "auto_stop_surplus_watts", 0.0),
            "auto_min_soc": getattr(self.service, "auto_min_soc", 0.0),
            "auto_resume_soc": getattr(self.service, "auto_resume_soc", 0.0),
            "auto_start_delay_seconds": getattr(self.service, "auto_start_delay_seconds", 0.0),
            "auto_stop_delay_seconds": getattr(self.service, "auto_stop_delay_seconds", 0.0),
            "auto_scheduled_enabled_days": str(
                getattr(self.service, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri")
            ),
            "auto_scheduled_fallback_delay_seconds": getattr(
                self.service,
                "auto_scheduled_night_start_delay_seconds",
                0.0,
            ),
            "auto_scheduled_latest_end_time": str(
                getattr(self.service, "auto_scheduled_latest_end_time", "06:30")
            ),
            "auto_scheduled_night_current": getattr(self.service, "auto_scheduled_night_current_amps", 0.0),
            "auto_dbus_backoff_base_seconds": getattr(self.service, "auto_dbus_backoff_base_seconds", 0.0),
            "auto_dbus_backoff_max_seconds": getattr(self.service, "auto_dbus_backoff_max_seconds", 0.0),
            "auto_grid_recovery_start_seconds": getattr(self.service, "auto_grid_recovery_start_seconds", 0.0),
            "auto_stop_surplus_delay_seconds": getattr(self.service, "auto_stop_surplus_delay_seconds", 0.0),
            "auto_stop_surplus_volatility_low_watts": getattr(
                self.service,
                "auto_stop_surplus_volatility_low_watts",
                0.0,
            ),
            "auto_stop_surplus_volatility_high_watts": getattr(
                self.service,
                "auto_stop_surplus_volatility_high_watts",
                0.0,
            ),
            "auto_reference_charge_power_watts": getattr(self.service, "auto_reference_charge_power_watts", 0.0),
            "auto_learn_charge_power_enabled": int(bool(getattr(self.service, "auto_learn_charge_power_enabled", True))),
            "auto_learn_charge_power_min_watts": getattr(self.service, "auto_learn_charge_power_min_watts", 0.0),
            "auto_learn_charge_power_alpha": getattr(self.service, "auto_learn_charge_power_alpha", 0.0),
            "auto_learn_charge_power_start_delay_seconds": getattr(
                self.service,
                "auto_learn_charge_power_start_delay_seconds",
                0.0,
            ),
            "auto_learn_charge_power_window_seconds": getattr(
                self.service,
                "auto_learn_charge_power_window_seconds",
                0.0,
            ),
            "auto_learn_charge_power_max_age_seconds": getattr(
                self.service,
                "auto_learn_charge_power_max_age_seconds",
                0.0,
            ),
            "auto_phase_switching": int(bool(getattr(self.service, "auto_phase_switching_enabled", True))),
            "auto_phase_prefer_lowest_when_idle": int(
                bool(getattr(self.service, "auto_phase_prefer_lowest_when_idle", True))
            ),
            "auto_phase_upshift_delay_seconds": getattr(self.service, "auto_phase_upshift_delay_seconds", 0.0),
            "auto_phase_downshift_delay_seconds": getattr(self.service, "auto_phase_downshift_delay_seconds", 0.0),
            "auto_phase_upshift_headroom_watts": getattr(self.service, "auto_phase_upshift_headroom_watts", 0.0),
            "auto_phase_downshift_margin_watts": getattr(self.service, "auto_phase_downshift_margin_watts", 0.0),
            "auto_phase_mismatch_retry_seconds": getattr(self.service, "auto_phase_mismatch_retry_seconds", 0.0),
            "auto_phase_mismatch_lockout_count": getattr(self.service, "auto_phase_mismatch_lockout_count", 0),
            "auto_phase_mismatch_lockout_seconds": getattr(self.service, "auto_phase_mismatch_lockout_seconds", 0.0),
        }

    def _connected_display(self, now: float | None) -> int:
        """Return the live GUI connected flag from configuration and backend reachability."""
        if not self._service_configured_for_connected(self.service):
            return 0
        return self._backend_reachable_display(self.service, now)

    @staticmethod
    def _service_configured_for_connected(service: object) -> bool:
        """Return whether the wallbox topology is configured enough to be shown connected."""
        topology_configured = getattr(service, "topology_configured", None)
        if topology_configured is not None:
            return bool(topology_configured)
        host_configured = getattr(service, "host_configured", None)
        if host_configured is not None:
            return bool(host_configured)
        return True

    @classmethod
    def _backend_reachable_display(cls, service: object, now: float | None) -> int:
        """Return the live backend reachability display value."""
        shelly_state = diagnostic_text(getattr(service, "_shelly_state", None)).lower()
        shelly_state_value = cls._explicit_connected_state_display(shelly_state)
        if shelly_state_value is not None:
            return shelly_state_value
        return cls._implicit_connected_display(service, now)

    @classmethod
    def _implicit_connected_display(cls, service: object, now: float | None) -> int:
        """Return the connected flag from readback freshness and transport failures."""
        if cls._fresh_backend_readback_present(service, now):
            return 1
        if cls._fresh_backend_transport_problem(service, now):
            return 0
        return int(finite_float_or_none(getattr(service, "_last_pm_status_at", None)) is None)

    @staticmethod
    def _connected_state_is_live(shelly_state: str) -> bool:
        """Return whether the backend state explicitly reports live reachability."""
        return shelly_state in {"online", "degraded"}

    @classmethod
    def _explicit_connected_state_display(cls, shelly_state: str) -> int | None:
        """Return a hard connected flag for explicit backend states."""
        if shelly_state == "offline":
            return 0
        if cls._connected_state_is_live(shelly_state):
            return 1
        return None

    @classmethod
    def _fresh_backend_readback_present(cls, service: object, now: float | None) -> bool:
        """Return whether PM or native charger readback has refreshed recently."""
        return any(
            cls._connected_timestamp_fresh(service, attribute_name, now)
            for attribute_name in (
                "_last_confirmed_pm_status_at",
                "_last_pm_status_at",
                "_last_charger_state_at",
                "_shelly_last_ok_at",
            )
        )

    @classmethod
    def _fresh_backend_transport_problem(cls, service: object, now: float | None) -> bool:
        """Return whether a recent transport failure should make the GUI disconnected."""
        return bool(getattr(service, "_last_charger_transport_reason", None)) and cls._connected_timestamp_fresh(
            service,
            "_last_charger_transport_at",
            now,
        )

    @classmethod
    def _connected_timestamp_fresh(cls, service: object, attribute_name: str, now: float | None) -> bool:
        """Return whether one backend timestamp is inside the connected freshness window."""
        timestamp = finite_float_or_none(getattr(service, attribute_name, None))
        if timestamp is None:
            return False
        current_time = time.time() if now is None else float(now)
        return current_time - float(timestamp) <= cls._connected_stale_after_seconds(service)

    @staticmethod
    def _connected_stale_after_seconds(service: object) -> float:
        """Return how long a non-native backend may be silent before the GUI shows disconnected."""
        soft_fail_seconds = finite_float_or_none(getattr(service, "auto_shelly_soft_fail_seconds", None))
        return max(1.0, float(soft_fail_seconds if soft_fail_seconds is not None else 10.0) * 2.0)

    def publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        """Publish configuration-like EV charger paths and refresh GUI controls periodically."""
        return self.core.publish_fields(
            "config",
            self._config_values(startstop_display, now),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
