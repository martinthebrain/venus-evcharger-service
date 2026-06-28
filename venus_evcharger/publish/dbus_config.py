# SPDX-License-Identifier: GPL-3.0-or-later
"""Config-value publishing helpers for DBus publishing."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any, TYPE_CHECKING

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.backend.models import effective_supported_phase_selections, switch_feedback_mismatch
from venus_evcharger.core.common import (
    DEFAULT_SCHEDULED_ENABLED_DAYS,
    evse_fault_reason,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)
from venus_evcharger.core.contracts import finite_float_or_none, normalized_auto_state_pair


class _DbusPublishConfigMixin:
    service: Any

    if TYPE_CHECKING:  # pragma: no cover
        # Sibling mixins provide these methods on the composed
        # DbusPublishController. Keeping the declarations type-check-only
        # preserves the runtime MRO while avoiding broad attr suppressions.

        def _charger_enabled_readback(self, now: float | None) -> bool | None: ...

        def _display_set_current(self, now: float | None) -> float: ...

        def ensure_state(self) -> None: ...

        def _publish_values_transactional(
            self,
            group_name: str,
            values: Mapping[str, Any],
            now: float | None,
            interval_seconds: float | None = None,
            force: bool = False,
        ) -> bool: ...

    def _config_values(self, startstop_display: int, now: float | None) -> dict[str, Any]:
        """Return mode and control values keyed by DBus path."""
        charger_enabled = self._charger_enabled_readback(now)
        current_time = time.time() if now is None else float(now)
        effective_supported = effective_supported_phase_selections(
            getattr(self.service, "supported_phase_selections", ("P1",)),
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
            "/Connected": self._connected_display(now),
            "/Status": int(getattr(self.service, "last_status", 0)),
            "/Mode": int(getattr(self.service, "virtual_mode", 0)),
            "/AutoStart": int(getattr(self.service, "virtual_autostart", 1)),
            "/StartStop": startstop_value,
            "/Enable": enable_display,
            "/PhaseSelection": str(getattr(self.service, "requested_phase_selection", "P1")),
            "/PhaseSelectionActive": str(getattr(self.service, "active_phase_selection", "P1")),
            "/SupportedPhaseSelections": ",".join(effective_supported),
            "/SetCurrent": self._display_set_current(now),
            "/MinCurrent": getattr(self.service, "min_current", 0.0),
            "/MaxCurrent": getattr(self.service, "max_current", 0.0),
            "/Auto/StartSurplusWatts": getattr(self.service, "auto_start_surplus_watts", 0.0),
            "/Auto/StopSurplusWatts": getattr(self.service, "auto_stop_surplus_watts", 0.0),
            "/Auto/MinSoc": getattr(self.service, "auto_min_soc", 0.0),
            "/Auto/ResumeSoc": getattr(self.service, "auto_resume_soc", 0.0),
            "/Auto/StartDelaySeconds": getattr(self.service, "auto_start_delay_seconds", 0.0),
            "/Auto/StopDelaySeconds": getattr(self.service, "auto_stop_delay_seconds", 0.0),
            "/Auto/ScheduledEnabledDays": str(
                getattr(self.service, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri")
            ),
            "/Auto/ScheduledFallbackDelaySeconds": getattr(
                self.service,
                "auto_scheduled_night_start_delay_seconds",
                0.0,
            ),
            "/Auto/ScheduledLatestEndTime": str(
                getattr(self.service, "auto_scheduled_latest_end_time", "06:30")
            ),
            "/Auto/ScheduledNightCurrent": getattr(self.service, "auto_scheduled_night_current_amps", 0.0),
            "/Auto/DbusBackoffBaseSeconds": getattr(self.service, "auto_dbus_backoff_base_seconds", 0.0),
            "/Auto/DbusBackoffMaxSeconds": getattr(self.service, "auto_dbus_backoff_max_seconds", 0.0),
            "/Auto/GridRecoveryStartSeconds": getattr(self.service, "auto_grid_recovery_start_seconds", 0.0),
            "/Auto/StopSurplusDelaySeconds": getattr(self.service, "auto_stop_surplus_delay_seconds", 0.0),
            "/Auto/StopSurplusVolatilityLowWatts": getattr(
                self.service,
                "auto_stop_surplus_volatility_low_watts",
                0.0,
            ),
            "/Auto/StopSurplusVolatilityHighWatts": getattr(
                self.service,
                "auto_stop_surplus_volatility_high_watts",
                0.0,
            ),
            "/Auto/ReferenceChargePowerWatts": getattr(self.service, "auto_reference_charge_power_watts", 0.0),
            "/Auto/LearnChargePowerEnabled": int(bool(getattr(self.service, "auto_learn_charge_power_enabled", True))),
            "/Auto/LearnChargePowerMinWatts": getattr(self.service, "auto_learn_charge_power_min_watts", 0.0),
            "/Auto/LearnChargePowerAlpha": getattr(self.service, "auto_learn_charge_power_alpha", 0.0),
            "/Auto/LearnChargePowerStartDelaySeconds": getattr(
                self.service,
                "auto_learn_charge_power_start_delay_seconds",
                0.0,
            ),
            "/Auto/LearnChargePowerWindowSeconds": getattr(
                self.service,
                "auto_learn_charge_power_window_seconds",
                0.0,
            ),
            "/Auto/LearnChargePowerMaxAgeSeconds": getattr(
                self.service,
                "auto_learn_charge_power_max_age_seconds",
                0.0,
            ),
            "/Auto/PhaseSwitching": int(bool(getattr(self.service, "auto_phase_switching_enabled", True))),
            "/Auto/PhasePreferLowestWhenIdle": int(
                bool(getattr(self.service, "auto_phase_prefer_lowest_when_idle", True))
            ),
            "/Auto/PhaseUpshiftDelaySeconds": getattr(self.service, "auto_phase_upshift_delay_seconds", 0.0),
            "/Auto/PhaseDownshiftDelaySeconds": getattr(self.service, "auto_phase_downshift_delay_seconds", 0.0),
            "/Auto/PhaseUpshiftHeadroomWatts": getattr(self.service, "auto_phase_upshift_headroom_watts", 0.0),
            "/Auto/PhaseDownshiftMarginWatts": getattr(self.service, "auto_phase_downshift_margin_watts", 0.0),
            "/Auto/PhaseMismatchRetrySeconds": getattr(self.service, "auto_phase_mismatch_retry_seconds", 0.0),
            "/Auto/PhaseMismatchLockoutCount": getattr(self.service, "auto_phase_mismatch_lockout_count", 0),
            "/Auto/PhaseMismatchLockoutSeconds": getattr(self.service, "auto_phase_mismatch_lockout_seconds", 0.0),
        }

    def _connected_display(self, now: float | None) -> int:
        """Return the live GUI connected flag from configuration and backend reachability."""
        if not self._service_configured_for_connected(self.service):
            return 0
        return self._backend_reachable_display(self.service, now)

    @staticmethod
    def _service_configured_for_connected(service: Any) -> bool:
        """Return whether the wallbox topology is configured enough to be shown connected."""
        return bool(getattr(service, "topology_configured", getattr(service, "host_configured", True)))

    @classmethod
    def _backend_reachable_display(cls, service: Any, now: float | None) -> int:
        """Return the live backend reachability display value."""
        shelly_state = str(getattr(service, "_shelly_state", "") or "").strip().lower()
        shelly_state_value = cls._explicit_connected_state_display(shelly_state)
        if shelly_state_value is not None:
            return shelly_state_value
        return cls._implicit_connected_display(service, now)

    @classmethod
    def _implicit_connected_display(cls, service: Any, now: float | None) -> int:
        """Return the connected flag from readback freshness and transport failures."""
        if cls._fresh_backend_readback_present(service, now):
            return 1
        if cls._fresh_backend_transport_problem(service, now):
            return 0
        return cls._recent_pm_status_connected(service, now)

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
    def _fresh_backend_readback_present(cls, service: Any, now: float | None) -> bool:
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
    def _fresh_backend_transport_problem(cls, service: Any, now: float | None) -> bool:
        """Return whether a recent transport failure should make the GUI disconnected."""
        return bool(getattr(service, "_last_charger_transport_reason", None)) and cls._connected_timestamp_fresh(
            service,
            "_last_charger_transport_at",
            now,
        )

    @classmethod
    def _connected_timestamp_fresh(cls, service: Any, attribute_name: str, now: float | None) -> bool:
        """Return whether one backend timestamp is inside the connected freshness window."""
        timestamp = finite_float_or_none(getattr(service, attribute_name, None))
        if timestamp is None:
            return False
        current_time = time.time() if now is None else float(now)
        return current_time - float(timestamp) <= cls._connected_stale_after_seconds(service)

    @classmethod
    def _recent_pm_status_connected(cls, service: Any, now: float | None) -> int:
        """Return whether recent metering readback still counts the backend as connected."""
        last_pm_status_at = finite_float_or_none(getattr(service, "_last_pm_status_at", None))
        if last_pm_status_at is None:
            return 1
        current_time = time.time() if now is None else float(now)
        stale_after_seconds = cls._connected_stale_after_seconds(service)
        return int(current_time - float(last_pm_status_at) <= stale_after_seconds)

    @staticmethod
    def _connected_stale_after_seconds(service: Any) -> float:
        """Return how long a non-native backend may be silent before the GUI shows disconnected."""
        soft_fail_seconds = finite_float_or_none(getattr(service, "auto_shelly_soft_fail_seconds", None))
        return max(1.0, float(soft_fail_seconds if soft_fail_seconds is not None else 10.0) * 2.0)

    @staticmethod
    def _backend_mode_value(service: Any) -> str:
        """Return one stable backend-mode label for diagnostics."""
        return backend_mode_for_service(service, "combined")

    @staticmethod
    def _backend_type_value(service: Any, attribute_name: str, default: str = "") -> str:
        """Return one stable backend-type label for diagnostics."""
        role_map = {
            "meter_backend_type": "meter",
            "switch_backend_type": "switch",
            "charger_backend_type": "charger",
        }
        role = role_map.get(attribute_name)
        if role is None:
            raw_value = getattr(service, attribute_name, default)
            normalized = str(raw_value).strip() if raw_value is not None else ""
            return normalized or default
        return backend_type_for_service(service, role, default)

    @staticmethod
    def _charger_current_target_value(service: Any) -> float:
        """Return the last applied native-charger current target or -1 when absent."""
        target_amps = finite_float_or_none(getattr(service, "_charger_target_current_amps", None))
        return -1.0 if target_amps is None else float(target_amps)

    @staticmethod
    def _auto_metrics(service: Any) -> dict[str, Any]:
        """Return the latest Auto metrics mapping used for outward diagnostics."""
        metrics = getattr(service, "_last_auto_metrics", None)
        if not isinstance(metrics, Mapping):
            return {}
        return {str(key): value for key, value in metrics.items()}

    @classmethod
    def _auto_phase_metric_text(cls, service: Any, field_name: str) -> str:
        """Return one outward-safe Auto phase metric text value."""
        raw_value = cls._auto_metrics(service).get(field_name)
        return "" if raw_value is None else str(raw_value).strip()

    @staticmethod
    def _diagnostic_text_value(raw_value: Any) -> str:
        """Return one stripped diagnostic text value or an empty string."""
        return "" if raw_value is None else str(raw_value).strip()

    @staticmethod
    def _fault_reason(service: Any) -> str:
        """Return the active hard EVSE-fault reason or an empty string."""
        reason = evse_fault_reason(getattr(service, "_last_health_reason", ""))
        return "" if reason is None else reason

    @classmethod
    def _fault_active(cls, service: Any) -> int:
        """Return whether a hard EVSE fault is currently active."""
        return int(bool(cls._fault_reason(service)))

    @staticmethod
    def _scheduled_snapshot(service: Any, now: float) -> Any | None:
        """Return the derived scheduled-mode snapshot when scheduled mode is active."""
        if not mode_uses_scheduled_logic(getattr(service, "virtual_mode", 0)):
            return None
        return scheduled_mode_snapshot(
            datetime.fromtimestamp(now),
            getattr(service, "auto_month_windows", {}),
            getattr(service, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            delay_seconds=float(getattr(service, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(service, "auto_scheduled_latest_end_time", "06:30"),
        )

    @staticmethod
    def _recovery_active(service: Any) -> int:
        """Return whether the broad Auto state is currently in recovery mode."""
        auto_state, _auto_state_code = normalized_auto_state_pair(
            getattr(service, "_last_auto_state", "idle"),
            getattr(service, "_last_auto_state_code", 0),
        )
        return int(auto_state == "recovery")

    @classmethod
    def _observed_phase_value(cls, service: Any) -> str:
        """Return the latest observed phase selection from PM status or charger readback."""
        pm_status = getattr(service, "_last_confirmed_pm_status", None)
        if isinstance(pm_status, Mapping):
            observed = cls._diagnostic_text_value(pm_status.get("_phase_selection"))
            if observed:
                return observed
        return cls._diagnostic_text_value(getattr(service, "_last_charger_state_phase_selection", None))

    @staticmethod
    def _phase_switch_mismatch_active(service: Any) -> int:
        """Return whether a phase-switch mismatch is currently active."""
        active = bool(getattr(service, "_phase_switch_mismatch_active", False))
        if active:
            return 1
        return int(str(getattr(service, "_last_health_reason", "")) == "phase-switch-mismatch")

    @staticmethod
    def _phase_switch_lockout_active(service: Any, now: float) -> int:
        """Return whether a phase-switch lockout is currently active."""
        lockout_selection = getattr(service, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(service, "_phase_switch_lockout_until", None))
        if lockout_selection is None or lockout_until is None:
            return 0
        return 1 if float(now) < lockout_until else 0

    @classmethod
    def _phase_switch_lockout_target(cls, service: Any, now: float) -> str:
        """Return the active phase-switch lockout target or an empty string."""
        if cls._phase_switch_lockout_active(service, now) == 0:
            return ""
        return cls._diagnostic_text_value(getattr(service, "_phase_switch_lockout_selection", None))

    @classmethod
    def _phase_switch_lockout_reason(cls, service: Any, now: float) -> str:
        """Return the active phase-switch lockout reason or an empty string."""
        if cls._phase_switch_lockout_active(service, now) == 0:
            return ""
        return cls._diagnostic_text_value(getattr(service, "_phase_switch_lockout_reason", None))

    @staticmethod
    def _phase_supported_configured(service: Any) -> str:
        """Return the configured supported phase selections without runtime degradation."""
        return ",".join(tuple(getattr(service, "supported_phase_selections", ("P1",))))

    @classmethod
    def _phase_supported_effective(cls, service: Any, now: float) -> str:
        """Return the effective supported phase selections after lockout degradation."""
        effective_supported = effective_supported_phase_selections(
            getattr(service, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(service, "_phase_switch_lockout_until", None),
            now=now,
        )
        return ",".join(effective_supported)

    @classmethod
    def _phase_degraded_active(cls, service: Any, now: float) -> int:
        """Return whether runtime phase support is currently degraded."""
        return int(cls._phase_supported_configured(service) != cls._phase_supported_effective(service, now))

    @staticmethod
    def _switch_feedback_closed(service: Any) -> int:
        """Return explicit switch feedback as 0/1, or -1 when unavailable."""
        feedback_closed = getattr(service, "_last_switch_feedback_closed", None)
        return -1 if feedback_closed is None else int(bool(feedback_closed))

    @staticmethod
    def _switch_interlock_ok(service: Any) -> int:
        """Return explicit switch interlock state as 0/1, or -1 when unavailable."""
        interlock_ok = getattr(service, "_last_switch_interlock_ok", None)
        return -1 if interlock_ok is None else int(bool(interlock_ok))

    @classmethod
    def _switch_feedback_mismatch(cls, service: Any) -> int:
        """Return whether explicit switch feedback currently disagrees with relay state."""
        feedback_closed = getattr(service, "_last_switch_feedback_closed", None)
        if feedback_closed is None:
            return int(str(getattr(service, "_last_health_reason", "")) == "contactor-feedback-mismatch")
        pm_status = getattr(service, "_last_confirmed_pm_status", None)
        relay_on = False if not isinstance(pm_status, Mapping) else bool(pm_status.get("output", False))
        return int(switch_feedback_mismatch(relay_on, feedback_closed))

    @staticmethod
    def _contactor_suspected_open(service: Any) -> int:
        """Return whether runtime currently suspects an open contactor without explicit feedback."""
        return int(str(getattr(service, "_last_health_reason", "")) == "contactor-suspected-open")

    @staticmethod
    def _contactor_suspected_welded(service: Any) -> int:
        """Return whether runtime currently suspects a welded contactor without explicit feedback."""
        return int(str(getattr(service, "_last_health_reason", "")) == "contactor-suspected-welded")

    @staticmethod
    def _contactor_lockout_reason(service: Any) -> str:
        """Return the active contactor-fault lockout reason or an empty string."""
        reason = str(getattr(service, "_contactor_lockout_reason", "") or "").strip()
        return reason

    @classmethod
    def _contactor_lockout_active(cls, service: Any) -> int:
        """Return whether a contactor-fault lockout is currently latched."""
        return int(bool(cls._contactor_lockout_reason(service)))

    @staticmethod
    def _contactor_lockout_source(service: Any) -> str:
        """Return the active contactor-fault lockout source or an empty string."""
        source = str(getattr(service, "_contactor_lockout_source", "") or "").strip()
        return source

    @classmethod
    def _contactor_fault_count(cls, service: Any) -> int:
        """Return the current contactor-fault counter for the active or latched reason."""
        counts = getattr(service, "_contactor_fault_counts", None)
        if not isinstance(counts, dict):
            return 0
        reason = cls._contactor_lockout_reason(service)
        if not reason:
            reason = str(getattr(service, "_contactor_fault_active_reason", "") or "").strip()
        if not reason:
            return 0
        return int(counts.get(reason, 0))

    @classmethod
    def _auto_phase_metric_float(cls, service: Any, field_name: str) -> float:
        """Return one outward-safe Auto phase metric float value or -1 when absent."""
        value = finite_float_or_none(cls._auto_metrics(service).get(field_name))
        return -1.0 if value is None else float(value)

    def publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        """Publish configuration-like EV charger paths and refresh GUI controls periodically."""
        self.ensure_state()
        return self._publish_values_transactional(
            "config",
            self._config_values(startstop_display, now),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
