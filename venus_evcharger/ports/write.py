# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any, cast


from venus_evcharger.backend.models import (
    PhaseSelection,
    normalize_phase_selection,
)
from venus_evcharger.core.contracts import (
    normalized_worker_snapshot,
)
from venus_evcharger.ports.write_runtime import _WriteControllerRuntimePortMixin
from .base import _BaseServicePort

class WriteControllerPort(_WriteControllerRuntimePortMixin, _BaseServicePort):
    """Expose only the write-path surface needed by ``DbusWriteController``."""

    _ALLOWED_ATTRS = {
        "virtual_mode",
        "virtual_autostart",
        "virtual_startstop",
        "virtual_enable",
        "virtual_set_current",
        "requested_phase_selection",
        "active_phase_selection",
        "supported_phase_selections",
        "min_current",
        "max_current",
        "auto_start_surplus_watts",
        "auto_stop_surplus_watts",
        "auto_min_soc",
        "auto_resume_soc",
        "auto_start_delay_seconds",
        "auto_stop_delay_seconds",
        "auto_scheduled_enabled_days",
        "auto_scheduled_night_start_delay_seconds",
        "auto_scheduled_latest_end_time",
        "auto_scheduled_night_current_amps",
        "auto_dbus_backoff_base_seconds",
        "auto_dbus_backoff_max_seconds",
        "auto_grid_recovery_start_seconds",
        "auto_stop_surplus_delay_seconds",
        "auto_stop_surplus_volatility_low_watts",
        "auto_stop_surplus_volatility_high_watts",
        "auto_reference_charge_power_watts",
        "auto_learn_charge_power_enabled",
        "auto_learn_charge_power_min_watts",
        "auto_learn_charge_power_alpha",
        "auto_learn_charge_power_start_delay_seconds",
        "auto_learn_charge_power_window_seconds",
        "auto_learn_charge_power_max_age_seconds",
        "auto_phase_upshift_delay_seconds",
        "auto_phase_downshift_delay_seconds",
        "auto_phase_upshift_headroom_watts",
        "auto_phase_downshift_margin_watts",
        "auto_phase_prefer_lowest_when_idle",
        "auto_phase_mismatch_retry_seconds",
        "auto_phase_mismatch_lockout_count",
        "auto_phase_mismatch_lockout_seconds",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "manual_override_until",
        "_software_update_run_requested_at",
    }
    _MUTABLE_ATTRS = _ALLOWED_ATTRS

    def __init__(self, service: Any) -> None:
        super().__init__(service)


    def clear_auto_samples(self) -> Any:
        return self._service._clear_auto_samples()

    def queue_relay_command(self, relay_on: bool, current_time: float) -> Any:
        return self._service._queue_relay_command(relay_on, current_time)

    def publish_local_pm_status(self, relay_on: bool, current_time: float) -> Any:
        return self._service._publish_local_pm_status(relay_on, current_time)

    def get_worker_snapshot(self) -> Any:
        return self._service._get_worker_snapshot()

    def _relay_status_freshness_seconds(self) -> float:
        """Return how old a confirmed relay sample may be for cutover decisions."""
        candidates = [2.0]
        worker_poll_seconds = getattr(self._service, "_worker_poll_interval_seconds", None)
        if worker_poll_seconds is not None and float(worker_poll_seconds) > 0:
            candidates.append(float(worker_poll_seconds) * 2.0)
        relay_sync_timeout_seconds = getattr(self._service, "relay_sync_timeout_seconds", None)
        if relay_sync_timeout_seconds is not None and float(relay_sync_timeout_seconds) > 0:
            candidates.append(float(relay_sync_timeout_seconds))
        return max(1.0, min(candidates))

    @staticmethod
    def _fresh_snapshot_output(snapshot: Any, current_time: float, max_age_seconds: float) -> bool | None:
        """Return a fresh confirmed relay output directly from the worker snapshot."""
        normalized_snapshot = normalized_worker_snapshot(snapshot, now=current_time, clamp_future_timestamps=False)
        pm_status = normalized_snapshot.get("pm_status")
        pm_confirmed = bool(normalized_snapshot.get("pm_confirmed", False))
        captured_at = normalized_snapshot.get("pm_captured_at", normalized_snapshot.get("captured_at"))
        if not WriteControllerPort._relay_output_payload_present(pm_confirmed, pm_status, captured_at):
            return None
        captured_at_value = WriteControllerPort._relay_output_timestamp(captured_at)
        if captured_at_value is None:
            return None
        if not WriteControllerPort._relay_output_timestamp_fresh(current_time, captured_at_value, max_age_seconds):
            return None
        return WriteControllerPort._relay_output_value(pm_status)

    def _fresh_last_output(self, current_time: float, max_age_seconds: float) -> bool | None:
        """Return a fresh confirmed relay output from the last remembered Shelly read."""
        last_pm_status, last_pm_status_at = self._last_relay_output_sample()
        if not self._relay_output_payload_present(True, last_pm_status, last_pm_status_at):
            return None
        last_pm_status_at_value = self._relay_output_timestamp(last_pm_status_at)
        if last_pm_status_at_value is None:
            return None
        if not self._relay_output_timestamp_fresh(current_time, last_pm_status_at_value, max_age_seconds):
            return None
        return self._relay_output_value(last_pm_status)

    def _last_relay_output_sample(self) -> tuple[Any, Any]:
        """Return the best remembered relay-output sample for cutover freshness checks."""
        last_pm_status = getattr(self._service, "_last_confirmed_pm_status", None)
        last_pm_status_at = getattr(self._service, "_last_confirmed_pm_status_at", None)
        if last_pm_status is not None:
            return last_pm_status, last_pm_status_at
        if bool(getattr(self._service, "_last_pm_status_confirmed", False)):
            return getattr(self._service, "_last_pm_status", None), getattr(self._service, "_last_pm_status_at", None)
        return None, None

    @staticmethod
    def _relay_output_payload_present(
        confirmed: bool,
        pm_status: Any,
        captured_at: Any,
    ) -> bool:
        """Return whether a relay-output payload has the required shape for freshness checks."""
        return bool(
            confirmed
            and isinstance(pm_status, dict)
            and "output" in pm_status
            and captured_at is not None
        )

    @staticmethod
    def _relay_output_timestamp(captured_at: Any) -> float | None:
        """Return a numeric relay-output timestamp when one is present and valid."""
        if not isinstance(captured_at, (int, float)) or isinstance(captured_at, bool):
            return None
        return float(captured_at)

    @staticmethod
    def _relay_output_timestamp_fresh(current_time: float, captured_at: float, max_age_seconds: float) -> bool:
        """Return whether a relay-output timestamp is fresh enough for cutover logic."""
        age_seconds = current_time - float(captured_at)
        return -1.0 <= age_seconds <= max_age_seconds

    @staticmethod
    def _relay_output_value(pm_status: Any) -> bool | None:
        """Return a normalized relay output from a status payload with an output field."""
        if not isinstance(pm_status, dict) or "output" not in pm_status:
            return None
        return bool(pm_status.get("output"))

    @staticmethod
    def _pending_relay_state_on(sample: Any) -> bool:
        """Return whether a pending relay sample requests relay-on."""
        if not isinstance(sample, tuple) or not sample:
            return False
        return bool(sample[0])

    def _fresh_confirmed_relay_output(self, snapshot: Any) -> bool | None:
        """Return the latest confirmed relay output only when it is still fresh."""
        current_time = self.time_now()
        max_age_seconds = self._relay_status_freshness_seconds()
        output = self._fresh_snapshot_output(snapshot, current_time, max_age_seconds)
        if output is not None:
            return output
        return self._fresh_last_output(current_time, max_age_seconds)

    def relay_may_be_on_for_cutover(self) -> bool:
        snapshot = self.get_worker_snapshot()
        peek_pending = getattr(self._service, "_peek_pending_relay_command", None)
        if callable(peek_pending):
            if self._pending_relay_state_on(peek_pending()):
                return True

        confirmed_output = self._fresh_confirmed_relay_output(snapshot)
        if confirmed_output is not None:
            return bool(confirmed_output)
        # Without a fresh confirmed relay sample, cutover must stay conservative.
        # A virtual/manual display state alone is not enough to prove the Shelly
        # relay is already off after startup or an external relay change.
        return True

    def update_worker_snapshot(self, **kwargs: Any) -> Any:
        return self._service._update_worker_snapshot(**kwargs)

    def publish_dbus_path(self, path: str, value: Any, current_time: float, force: bool = False) -> Any:
        return self._service._publish_dbus_path(path, value, current_time, force=force)

    def time_now(self) -> float:
        return float(self._service._time_now())

    def charger_enable_available(self) -> bool:
        backend = getattr(self._service, "_charger_backend", None)
        return backend is not None and hasattr(backend, "set_enabled")

    def charger_current_available(self) -> bool:
        backend = getattr(self._service, "_charger_backend", None)
        return backend is not None and hasattr(backend, "set_current")

    def charger_set_enabled(self, enabled: bool) -> Any:
        backend = getattr(self._service, "_charger_backend", None)
        if backend is None or not hasattr(backend, "set_enabled"):
            raise RuntimeError("No charger backend with set_enabled configured")
        return backend.set_enabled(bool(enabled))

    def charger_set_current(self, amps: float) -> Any:
        backend = getattr(self._service, "_charger_backend", None)
        if backend is None or not hasattr(backend, "set_current"):
            raise RuntimeError("No charger backend with set_current configured")
        return backend.set_current(float(amps))

    def phase_selection_requires_pause(self) -> bool:
        return bool(self._service._phase_selection_requires_pause())

    def apply_phase_selection(self, selection: Any) -> str:
        return cast(str, self._service._apply_phase_selection(selection))

    def normalize_phase_selection(self, value: Any, default: str | None = None) -> str:
        fallback: PhaseSelection = (
            cast(PhaseSelection, self.supported_phase_selections[0])
            if default is None
            else cast(PhaseSelection, str(default))
        )
        normalized: PhaseSelection = normalize_phase_selection(value, fallback)
        return str(normalized)

    def normalize_mode(self, value: Any) -> int:
        return int(self._service._normalize_mode(value))

    def mode_uses_auto_logic(self, mode: Any) -> bool:
        return bool(self._service._mode_uses_auto_logic(mode))

    def state_summary(self) -> str:
        return cast(str, self._service._state_summary())

    def save_runtime_state(self) -> Any:
        return self._service._save_runtime_state()

    def save_runtime_overrides(self) -> Any:
        save_overrides = getattr(self._service, "_save_runtime_overrides", None)
        if callable(save_overrides):
            return save_overrides()
        return None

    def validate_runtime_config(self) -> Any:
        validate_runtime = getattr(self._service, "_validate_runtime_config", None)
        if callable(validate_runtime):
            return validate_runtime()
        return None
