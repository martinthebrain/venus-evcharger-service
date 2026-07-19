# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from venus_evcharger.backend.models import (
    normalize_phase_selection,
)
from venus_evcharger.core.contracts import (
    normalized_worker_snapshot,
)
from venus_evcharger.core.return_contracts import require_str
from venus_evcharger.ports.write_runtime import WriteControllerRuntimePort


class WriteControllerPort(WriteControllerRuntimePort):
    """Expose only the write-path surface needed by ``DbusWriteController``."""

    def __init__(self, service: Any) -> None:
        super().__init__(service)

    def clear_auto_samples(self) -> object:
        return self._service.auto.clear_samples()

    def queue_relay_command(self, relay_on: bool, current_time: float) -> object:
        return self._service.runtime.queue_relay_command(relay_on, current_time)

    def publish_local_pm_status(self, relay_on: bool, current_time: float) -> object:
        return self._service.runtime.publish_local_pm_status(relay_on, current_time)

    def get_worker_snapshot(self) -> object:
        return self._service.runtime.worker_snapshot()

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
    def _fresh_snapshot_output(snapshot: object, current_time: float, max_age_seconds: float) -> bool | None:
        """Return a fresh confirmed relay output directly from the worker snapshot."""
        normalized_snapshot = normalized_worker_snapshot(snapshot, now=current_time, clamp_future_timestamps=False)
        pm_status = normalized_snapshot.get("pm_status")
        pm_confirmed = bool(normalized_snapshot["pm_confirmed"])
        captured_at = normalized_snapshot["pm_captured_at"]
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

    def _last_relay_output_sample(self) -> tuple[object, object]:
        """Return the canonical confirmed relay-output sample."""
        service_state = vars(self._service)
        return (
            service_state.get("_last_confirmed_pm_status"),
            service_state.get("_last_confirmed_pm_status_at"),
        )

    @staticmethod
    def _relay_output_payload_present(
        confirmed: bool,
        pm_status: object,
        captured_at: object,
    ) -> bool:
        """Return whether a relay-output payload has the required shape for freshness checks."""
        return bool(
            confirmed
            and isinstance(pm_status, dict)
            and "output" in pm_status
            and captured_at is not None
        )

    @staticmethod
    def _relay_output_timestamp(captured_at: object) -> float | None:
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
    def _relay_output_value(pm_status: object) -> bool | None:
        """Return a normalized relay output from a status payload with an output field."""
        if not isinstance(pm_status, dict) or "output" not in pm_status:
            return None
        return bool(pm_status.get("output"))

    @staticmethod
    def _pending_relay_state_on(sample: object) -> bool:
        """Return whether a pending relay sample requests relay-on."""
        if not isinstance(sample, tuple) or not sample:
            return False
        return bool(sample[0])

    def _fresh_confirmed_relay_output(self, snapshot: object) -> bool | None:
        """Return the latest confirmed relay output only when it is still fresh."""
        current_time = self.time_now()
        max_age_seconds = self._relay_status_freshness_seconds()
        output = self._fresh_snapshot_output(snapshot, current_time, max_age_seconds)
        if output is not None:
            return output
        return self._fresh_last_output(current_time, max_age_seconds)

    def relay_may_be_on_for_cutover(self) -> bool:
        snapshot = self.get_worker_snapshot()
        if self._pending_relay_state_on(self._service.runtime.pending_relay_command()):
            return True

        confirmed_output = self._fresh_confirmed_relay_output(snapshot)
        if confirmed_output is not None:
            return bool(confirmed_output)
        # Without a fresh confirmed relay sample, cutover must stay conservative.
        # A virtual/manual display state alone is not enough to prove the Shelly
        # relay is already off after startup or an external relay change.
        return True

    def update_worker_snapshot(self, **kwargs: object) -> object:
        return self._service.runtime.update_worker_snapshot(**kwargs)

    def publish_dbus_field(self, field: str, value: object, current_time: float, force: bool = False) -> object:
        return self._service.state.publish_field(field, value, current_time, force=force)

    def time_now(self) -> float:
        return float(self._service.time_now())

    def charger_enable_available(self) -> bool:
        return self._charger_backend_method("set_enabled") is not None

    def charger_current_available(self) -> bool:
        return self._charger_backend_method("set_current") is not None

    def _charger_backend_method(self, method_name: str) -> Callable[..., object] | None:
        backend = getattr(self._service, "_charger_backend", None)
        method = None if backend is None else getattr(backend, method_name, None)
        return method if callable(method) else None

    def charger_set_enabled(self, enabled: bool) -> object:
        set_enabled = self._charger_backend_method("set_enabled")
        if set_enabled is None:
            raise RuntimeError("No charger backend with set_enabled configured")
        return set_enabled(bool(enabled))

    def charger_set_current(self, amps: float) -> object:
        set_current = self._charger_backend_method("set_current")
        if set_current is None:
            raise RuntimeError("No charger backend with set_current configured")
        return set_current(float(amps))

    def phase_selection_requires_pause(self) -> bool:
        return bool(self._service.runtime.phase_selection_requires_pause())

    def apply_phase_selection(self, selection: object) -> str:
        return require_str(self._service.runtime.apply_phase_selection(selection), "apply_phase_selection")

    def normalize_phase_selection(self, value: object, default: str | None = None) -> str:
        fallback_source = self.supported_phase_selections[0] if default is None else default
        fallback = normalize_phase_selection(fallback_source)
        return normalize_phase_selection(value, fallback)

    def normalize_mode(self, value: object) -> int:
        return int(self._service.auto.normalize_mode(value))

    def mode_uses_auto_logic(self, mode: object) -> bool:
        return bool(self._service.auto.mode_uses_auto_logic(mode))

    def state_summary(self) -> str:
        return require_str(self._service.state.summary(), "state.summary")

    def save_runtime_state(self) -> object:
        return self._service.state.save_runtime_state()

    def save_runtime_overrides(self) -> None:
        self._service.state.save_runtime_overrides()

    def validate_runtime_config(self) -> None:
        self._service.state.validate_runtime_config()
