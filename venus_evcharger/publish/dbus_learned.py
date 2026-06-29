# SPDX-License-Identifier: GPL-3.0-or-later
"""Learned-current and charger-readback display helpers for DBus publishing."""

from __future__ import annotations

import math
import time
from typing import Any

from venus_evcharger.core.common import (
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_retry_until,
    _fresh_charger_transport_detail,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_charger_transport_timestamp,
)
from venus_evcharger.core.contracts import (
    finite_float_or_none,
    normalize_binary_flag,
    normalize_learning_phase,
    normalize_learning_state,
)
from venus_evcharger.publish.dbus_config import _DbusPublishConfig
from venus_evcharger.publish.dbus_shared import _LearnedDisplayCurrentInputs


class _DbusPublishLearned(_DbusPublishConfig):
    service: Any

    def _display_uses_learned_set_current(self) -> bool:
        """Return whether the GUI SetCurrent field should mirror the learned EVSE current."""
        charger_backend = getattr(self.service, "_charger_backend", None)
        if charger_backend is not None and hasattr(charger_backend, "set_current"):
            return False
        return bool(normalize_binary_flag(getattr(self.service, "display_learned_set_current", 1), 1))

    def _charger_state_max_age_seconds(self) -> float:
        """Return how fresh charger readback must be before it overrides display state."""
        candidates = [2.0]
        live_interval = finite_float_or_none(
            getattr(self.service, "_dbus_live_publish_interval_seconds", 1.0)
        )
        if live_interval is not None and live_interval > 0.0:
            candidates.append(float(live_interval) * 2.0)
        soft_fail_seconds = finite_float_or_none(
            getattr(self.service, "auto_shelly_soft_fail_seconds", 10.0)
        )
        if soft_fail_seconds is not None and soft_fail_seconds > 0.0:
            candidates.append(float(soft_fail_seconds))
        return max(1.0, min(candidates))

    def _charger_state_fresh(self, now: float | None) -> bool:
        """Return whether a native charger readback is fresh enough to drive GUI state."""
        if getattr(self.service, "_charger_backend", None) is None:
            return False
        state_at = finite_float_or_none(getattr(self.service, "_last_charger_state_at", None))
        if state_at is None:
            return False
        current = time.time() if now is None else float(now)
        return abs(current - state_at) <= self._charger_state_max_age_seconds()

    def _charger_enabled_readback(self, now: float | None) -> bool | None:
        """Return fresh native charger enabled-state readback when available."""
        if not self._charger_state_fresh(now):
            return None
        raw_value = getattr(self.service, "_last_charger_state_enabled", None)
        return None if raw_value is None else bool(raw_value)

    def _charger_current_readback(self, now: float | None) -> float | None:
        """Return fresh native charger current readback when available."""
        if not self._charger_state_fresh(now):
            return None
        current_amps = finite_float_or_none(getattr(self.service, "_last_charger_state_current_amps", None))
        if current_amps is None or current_amps <= 0.0:
            return None
        return float(current_amps)

    def _charger_text_observed(self, attribute_name: str) -> str:
        """Return the last observed charger text field for diagnostics."""
        raw_value = getattr(self.service, attribute_name, None)
        text = str(raw_value).strip() if raw_value is not None else ""
        return text

    def _charger_estimate_active(self) -> int:
        """Return whether meterless charger values are currently estimated."""
        return int(bool(getattr(self.service, "_last_charger_estimate_source", None)))

    def _charger_estimate_source(self) -> str:
        """Return the current charger estimate source label for diagnostics."""
        return self._diagnostic_text_value(getattr(self.service, "_last_charger_estimate_source", None))

    def _charger_transport_active(self, now: float) -> int:
        """Return whether a charger-transport issue is currently active."""
        return int(_fresh_charger_transport_timestamp(self.service, now) is not None)

    def _charger_transport_reason(self, now: float) -> str:
        """Return the current charger-transport reason label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_transport_reason(self.service, now))

    def _charger_transport_source(self, now: float) -> str:
        """Return the current charger-transport source label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_transport_source(self.service, now))

    def _charger_transport_detail(self, now: float) -> str:
        """Return the current charger-transport detail text for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_transport_detail(self.service, now))

    def _charger_retry_active(self, now: float) -> int:
        """Return whether charger retry backoff is currently active."""
        return int(_fresh_charger_retry_until(self.service, now) is not None)

    def _charger_retry_reason(self, now: float) -> str:
        """Return the current charger retry reason label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_retry_reason(self.service, now))

    def _charger_retry_source(self, now: float) -> str:
        """Return the current charger retry source label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_retry_source(self.service, now))

    def _learned_charge_power_expired_for_display(self, now: float | None) -> bool:
        """Return True when learned charging power is too old for GUI display reuse."""
        max_age_seconds = finite_float_or_none(
            getattr(self.service, "auto_learn_charge_power_max_age_seconds", 21600.0)
        )
        if max_age_seconds is None or max_age_seconds <= 0:
            return False
        updated_at = finite_float_or_none(getattr(self.service, "learned_charge_power_updated_at", None))
        if updated_at is None:
            return True
        current = time.time() if now is None else float(now)
        return (current - updated_at) > max_age_seconds

    def _learned_display_current_allowed(self, now: float | None) -> bool:
        """Return whether learned charging power may currently drive the GUI current display."""
        if not self._display_uses_learned_set_current():
            return False
        if normalize_learning_state(getattr(self.service, "learned_charge_power_state", "unknown")) != "stable":
            return False
        return not self._learned_charge_power_expired_for_display(now)

    @staticmethod
    def _validated_learned_display_scalars(
        learned_power: float | None,
        voltage: float | None,
    ) -> tuple[float, float] | None:
        """Return validated positive learned-power scalars for display-current derivation."""
        if learned_power is None or learned_power <= 0 or voltage is None or voltage <= 0:
            return None
        return float(learned_power), float(voltage)

    def _learned_display_phase(self) -> str | None:
        """Return the normalized learned/display phase signature."""
        return normalize_learning_phase(
            getattr(self.service, "learned_charge_power_phase", getattr(self.service, "phase", "L1"))
        )

    def _raw_learned_display_values(self) -> tuple[float, float, str] | None:
        """Return raw learned display values before phase-voltage normalization."""
        scalars = self._validated_learned_display_scalars(
            finite_float_or_none(getattr(self.service, "learned_charge_power_watts", None)),
            finite_float_or_none(getattr(self.service, "learned_charge_power_voltage", None)),
        )
        phase = self._learned_display_phase()
        if scalars is None or phase is None:
            return None
        learned_power, voltage = scalars
        return learned_power, voltage, phase

    def _stable_learned_display_inputs(self, now: float | None) -> _LearnedDisplayCurrentInputs | None:
        """Return the validated inputs used to derive SetCurrent from learned charging power."""
        if not self._learned_display_current_allowed(now):
            return None
        raw_values = self._raw_learned_display_values()
        if raw_values is None:
            return None
        learned_power, voltage, phase = raw_values
        phase_voltage = self._phase_voltage_for_display_current(voltage, phase)
        if phase_voltage is None:
            return None
        return _LearnedDisplayCurrentInputs(
            power_w=learned_power,
            phase_voltage_v=phase_voltage,
            phase_count=3.0 if phase == "3P" else 1.0,
        )

    def _phase_voltage_for_display_current(self, voltage: float, phase: str) -> float | None:
        """Return the per-phase voltage used for learned-current display derivation."""
        phase_voltage = float(voltage)
        if phase == "3P" and str(getattr(self.service, "voltage_mode", "phase")).strip().lower() != "phase":
            phase_voltage = phase_voltage / math.sqrt(3.0)
        return None if phase_voltage <= 0 else phase_voltage

    @staticmethod
    def _rounded_display_current(current_amps: float) -> float | None:
        """Return one positive rounded display current or None when unusable."""
        rounded_current = finite_float_or_none(round(current_amps))
        if rounded_current is None or rounded_current <= 0:
            return None
        return float(rounded_current)

    def _clamped_display_current(self, current_amps: float) -> float:
        """Clamp one display current to the configured min/max current range."""
        normalized_current = float(current_amps)
        min_current = finite_float_or_none(getattr(self.service, "min_current", None))
        max_current = finite_float_or_none(getattr(self.service, "max_current", None))
        if min_current is not None:
            normalized_current = max(normalized_current, min_current)
        if max_current is not None and max_current > 0:
            normalized_current = min(normalized_current, max_current)
        return float(normalized_current)

    def _derived_learned_set_current(self, now: float | None) -> float | None:
        """Return one rounded display current derived from the stable learned charging power."""
        inputs = self._stable_learned_display_inputs(now)
        if inputs is None:
            return None
        display_current = inputs.power_w / (inputs.phase_voltage_v * inputs.phase_count)
        rounded_current = self._rounded_display_current(display_current)
        if rounded_current is None:
            return None
        return self._clamped_display_current(rounded_current)

    def _display_set_current(self, now: float | None) -> float:
        """Return the GUI-facing SetCurrent value with learned-current display fallback."""
        charger_current = self._charger_current_readback(now)
        if charger_current is not None:
            return charger_current
        learned_current = self._derived_learned_set_current(now)
        if learned_current is not None:
            return learned_current
        return float(self.service.virtual_set_current)
