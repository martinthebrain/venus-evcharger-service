# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for learned charging-power tracking in the update cycle."""

from __future__ import annotations

import logging
from typing import Any

from venus_evcharger.core.controller_contracts import ComposableControllerRole as _ComposableControllerRole


class _UpdateCycleLearningSupport(_ComposableControllerRole):
    def _signature_checked_session_started_at(self) -> float | None:
        """Return the stored session marker for the last signature check."""
        checked_session_started_at = getattr(
            self.service,
            "learned_charge_power_signature_checked_session_started_at",
            None,
        )
        return None if checked_session_started_at is None else float(checked_session_started_at)

    def _signature_session_delay_elapsed(self, current_session_started_at: float, now: float) -> bool:
        """Return whether the current session is old enough for signature checks."""
        minimum_seconds = float(getattr(self.service, "auto_learn_charge_power_start_delay_seconds", 30.0))
        return (float(now) - current_session_started_at) >= minimum_seconds

    def _signature_session_already_checked(self, current_session_started_at: float) -> bool:
        """Return whether the current charging session already ran one signature check."""
        checked_session_started_at = self._signature_checked_session_started_at()
        return checked_session_started_at is not None and checked_session_started_at == current_session_started_at

    def _stable_learned_power(self) -> float | None:
        """Return the current learned power only when the stored state is stable."""
        learned_power = getattr(self.service, "learned_charge_power_watts", None)
        current_state = self._normalize_learned_charge_power_state(
            getattr(self.service, "learned_charge_power_state", "unknown")
        )
        if current_state != "stable" or learned_power is None or float(learned_power) <= 0:
            return None
        return float(learned_power)

    def _signature_preserving_snapshot(self) -> dict[str, Any]:
        """Return the current learned-power signature fields in normalized form."""
        svc = self.service
        return {
            "phase_signature": self._normalize_learned_charge_power_phase(
                getattr(svc, "learned_charge_power_phase", None)
            ),
            "voltage_signature": getattr(svc, "learned_charge_power_voltage", None),
            "signature_mismatch_sessions": max(
                0,
                int(getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0)),
            ),
            "checked_session_started_at": getattr(
                svc,
                "learned_charge_power_signature_checked_session_started_at",
                None,
            ),
        }

    def _clear_learning_tracking(self) -> bool:
        """Clear the learned-power state and reset tracking metadata."""
        return bool(
            self._set_learning_tracking(
            self.service,
            state="unknown",
            learned_power=None,
            updated_at=None,
            learning_since=None,
            sample_count=0,
            phase_signature=None,
            voltage_signature=None,
            signature_mismatch_sessions=0,
            checked_session_started_at=None,
            )
        )

    def _stable_sample_count(self) -> int:
        """Return the persisted sample count clamped to the stable minimum."""
        current = int(getattr(self.service, "learned_charge_power_sample_count", 0))
        return int(max(self.LEARNED_POWER_STABLE_MIN_SAMPLES, current))

    def _phase_change_reset(
        self,
        stored_phase_signature: str | None,
        current_phase_signature: str | None,
    ) -> bool | None:
        """Reset learned power when the configured charging phase changed."""
        if (
            stored_phase_signature is None
            or current_phase_signature is None
            or stored_phase_signature == current_phase_signature
        ):
            return None
        logging.warning(
            "Discarding learned charge power after phase signature changed from %s to %s",
            stored_phase_signature,
            current_phase_signature,
        )
        return self._clear_learning_tracking()

    def _apply_stable_learning(
        self,
        learned_power: float,
        *,
        updated_at: float | None,
        phase_signature: str | None,
        voltage_signature: float | None,
        signature_mismatch_sessions: int,
        checked_session_started_at: float | None,
    ) -> bool:
        """Persist one stable learned-power snapshot."""
        return bool(
            self._set_learning_tracking(
            self.service,
            state="stable",
            learned_power=learned_power,
            updated_at=updated_at,
            learning_since=None,
            sample_count=self._stable_sample_count(),
            phase_signature=phase_signature,
            voltage_signature=voltage_signature,
            signature_mismatch_sessions=signature_mismatch_sessions,
            checked_session_started_at=checked_session_started_at,
            )
        )

    def _eligible_signature_session_started_at(self, relay_on: bool, now: float) -> float | None:
        """Return the current charging-session start when signature checks may run."""
        charging_started_at = getattr(self.service, "charging_started_at", None)
        if not relay_on or charging_started_at is None:
            return None
        current_session_started_at = float(charging_started_at)
        if not self._signature_session_delay_elapsed(current_session_started_at, now):
            return None
        if self._signature_session_already_checked(current_session_started_at):
            return None
        return current_session_started_at

    def _signature_mismatch_reasons(
        self,
        power: float,
        voltage: float,
        learned_power: float,
    ) -> tuple[list[str], float | None]:
        """Return active signature mismatch reasons and the current voltage signature."""
        mismatch_reasons: list[str] = []
        stored_voltage_signature = getattr(self.service, "learned_charge_power_voltage", None)
        current_voltage_signature = self._current_learning_voltage_signature(voltage)
        if (
            stored_voltage_signature is not None
            and current_voltage_signature is not None
            and abs(float(current_voltage_signature) - float(stored_voltage_signature))
            > self._voltage_signature_tolerance(float(stored_voltage_signature))
        ):
            mismatch_reasons.append("voltage")
        if abs(float(power) - float(learned_power)) > self._learning_stability_tolerance(float(learned_power)):
            mismatch_reasons.append("power")
        return mismatch_reasons, current_voltage_signature

    def _apply_signature_reconcile_result(
        self,
        learned_power: float,
        power: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        current_session_started_at: float,
        mismatch_reasons: list[str],
    ) -> bool:
        """Persist the outcome of one session signature reconciliation pass."""
        signature_snapshot = self._signature_preserving_snapshot()
        if not mismatch_reasons:
            return self._stable_signature_reconcile_result(
                learned_power,
                current_phase_signature,
                current_session_started_at,
                signature_snapshot,
            )
        return self._mismatching_signature_reconcile_result(
            learned_power,
            power,
            current_phase_signature,
            current_voltage_signature,
            current_session_started_at,
            mismatch_reasons,
            signature_snapshot,
        )

    def _stable_signature_reconcile_result(
        self,
        learned_power: float,
        current_phase_signature: str | None,
        current_session_started_at: float,
        signature_snapshot: dict[str, Any],
    ) -> bool:
        """Persist one successful per-session signature check."""
        return self._apply_stable_learning(
            learned_power,
            updated_at=getattr(self.service, "learned_charge_power_updated_at", None),
            phase_signature=signature_snapshot["phase_signature"] or current_phase_signature,
            voltage_signature=signature_snapshot["voltage_signature"],
            signature_mismatch_sessions=0,
            checked_session_started_at=current_session_started_at,
        )

    @staticmethod
    def _signature_mismatch_count(signature_snapshot: dict[str, Any]) -> int:
        """Return the incremented mismatch-session count for one signature snapshot."""
        return int(signature_snapshot["signature_mismatch_sessions"]) + 1

    @staticmethod
    def _signature_reason_label(mismatch_reasons: list[str]) -> str:
        """Return one human-readable mismatch reason list."""
        return ", ".join(mismatch_reasons)

    @staticmethod
    def _rounded_signature_value(value: float | None) -> float | None:
        """Return one rounded signature value when available."""
        return None if value is None else round(float(value), 1)

    def _log_terminal_signature_mismatch(
        self,
        mismatch_sessions: int,
        reason_label: str,
        learned_power: float,
        power: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        signature_snapshot: dict[str, Any],
    ) -> None:
        """Log the final warning before one learned signature is discarded."""
        logging.warning(
            "Discarding learned charge power after %s mismatching sessions (%s): learned=%sW measured=%sW phase=%s/%s voltage=%s/%sV",
            mismatch_sessions,
            reason_label,
            round(learned_power, 1),
            round(float(power), 1),
            signature_snapshot["phase_signature"],
            current_phase_signature,
            self._rounded_signature_value(signature_snapshot["voltage_signature"]),
            self._rounded_signature_value(current_voltage_signature),
        )

    def _mismatching_signature_reconcile_result(
        self,
        learned_power: float,
        power: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        current_session_started_at: float,
        mismatch_reasons: list[str],
        signature_snapshot: dict[str, Any],
    ) -> bool:
        """Persist one mismatching per-session signature check."""
        mismatch_sessions = self._signature_mismatch_count(signature_snapshot)
        reason_label = self._signature_reason_label(mismatch_reasons)
        if mismatch_sessions >= self.LEARNED_POWER_SIGNATURE_MISMATCH_SESSIONS:
            self._log_terminal_signature_mismatch(
                mismatch_sessions,
                reason_label,
                learned_power,
                power,
                current_phase_signature,
                current_voltage_signature,
                signature_snapshot,
            )
            return self._clear_learning_tracking()
        logging.info(
            "Observed learned charge-power signature mismatch session %s/%s (%s)",
            mismatch_sessions,
            self.LEARNED_POWER_SIGNATURE_MISMATCH_SESSIONS,
            reason_label,
        )
        return self._apply_stable_learning(
            learned_power,
            updated_at=getattr(self.service, "learned_charge_power_updated_at", None),
            phase_signature=signature_snapshot["phase_signature"] or current_phase_signature,
            voltage_signature=signature_snapshot["voltage_signature"],
            signature_mismatch_sessions=mismatch_sessions,
            checked_session_started_at=current_session_started_at,
        )

    def _learning_window_status(self, now: float) -> tuple[str, float | None]:
        """Return whether the current charging session is ready for learning samples."""
        charging_started_at = getattr(self.service, "charging_started_at", None)
        if charging_started_at is None:
            return "waiting", None
        session_started_at = float(charging_started_at)
        minimum_seconds = float(getattr(self.service, "auto_learn_charge_power_start_delay_seconds", 30.0))
        if (float(now) - session_started_at) < minimum_seconds:
            return "waiting", None
        learning_window_seconds = float(getattr(self.service, "auto_learn_charge_power_window_seconds", 180.0))
        if learning_window_seconds > 0 and (float(now) - session_started_at) > (minimum_seconds + learning_window_seconds):
            return "expired", session_started_at
        return "ready", session_started_at

    def _accepted_learning_sample(self, power: float, voltage: float) -> float | None:
        """Return one plausible learning sample or ``None`` when it should be ignored."""
        measured_power = float(power)
        if measured_power < float(getattr(self.service, "auto_learn_charge_power_min_watts", 500.0)):
            return None
        if measured_power > self._plausible_learning_power_max(voltage):
            return None
        return measured_power

    def _learning_session_result(
        self,
        enabled: bool,
        pm_confirmed: bool,
        relay_on: bool,
        status: int,
        now: float,
        current_state: str,
    ) -> tuple[float | None, bool | None]:
        """Return an eligible session start or the immediate learning decision."""
        if self._learning_measurement_ignored(enabled, pm_confirmed):
            return None, False
        if not self._learning_measurement_active(relay_on, status):
            return None, self._inactive_learning_session_decision(current_state)
        learning_window_status, charging_started_at = self._learning_window_status(now)
        if learning_window_status == "ready":
            return charging_started_at, None
        return None, self._windowed_learning_session_decision(learning_window_status, current_state)

    @staticmethod
    def _learning_measurement_ignored(enabled: bool, pm_confirmed: bool) -> bool:
        """Return whether learning should ignore the current measurement immediately."""
        return not enabled or not pm_confirmed

    @staticmethod
    def _learning_measurement_active(relay_on: bool, status: int) -> bool:
        """Return whether the current measurement represents active charging."""
        return bool(relay_on) and int(status) == 2

    def _inactive_learning_session_decision(self, current_state: str) -> bool:
        """Return the learning result when charging is not actively running."""
        if current_state != "learning":
            return False
        return self._clear_learning_tracking()

    def _windowed_learning_session_decision(
        self,
        learning_window_status: str,
        current_state: str,
    ) -> bool:
        """Return the learning result after inspecting the current learning window state."""
        if learning_window_status == "expired" and current_state == "learning":
            return self._clear_learning_tracking()
        return False

    def _should_restart_learning(
        self,
        current_state: str,
        previous: float | None,
        now: float,
    ) -> bool:
        """Return whether learning must restart from the next accepted sample."""
        return (
            current_state in {"unknown", "stale"}
            or previous is None
            or previous <= 0
            or self._is_learned_charge_power_stale(now)
        )

    def _smoothed_learning_values(
        self,
        previous_value: float,
        measured_power: float,
        current_voltage_signature: float | None,
    ) -> tuple[float, float | None]:
        """Return EWMA-smoothed learned power and voltage signature."""
        alpha = float(getattr(self.service, "auto_learn_charge_power_alpha", 0.2))
        learned_power = previous_value + alpha * (measured_power - previous_value)
        previous_voltage_signature = getattr(self.service, "learned_charge_power_voltage", None)
        if current_voltage_signature is None:
            learned_voltage_signature = previous_voltage_signature
        elif previous_voltage_signature is None or float(previous_voltage_signature) <= 0:
            learned_voltage_signature = current_voltage_signature
        else:
            learned_voltage_signature = float(previous_voltage_signature) + alpha * (
                float(current_voltage_signature) - float(previous_voltage_signature)
            )
        return learned_power, learned_voltage_signature

    def _restart_learning_sample(
        self,
        measured_power: float,
        now: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
    ) -> bool:
        """Start or restart the learning window from the current sample."""
        return bool(
            self._set_learning_tracking(
            self.service,
            state="learning",
            learned_power=measured_power,
            updated_at=now,
            learning_since=now,
            sample_count=1,
            phase_signature=current_phase_signature,
            voltage_signature=current_voltage_signature,
            signature_mismatch_sessions=0,
            checked_session_started_at=None,
            )
        )

    def _apply_learning_progress(
        self,
        measured_power: float,
        previous_value: float,
        learned_power: float,
        learned_voltage_signature: float | None,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        charging_started_at: float,
        now: float,
    ) -> bool:
        """Advance learning from one accepted sample after smoothing."""
        learning_since = getattr(self.service, "learned_charge_power_learning_since", None)
        if learning_since is None:
            learning_since = now
        sample_count = max(1, int(getattr(self.service, "learned_charge_power_sample_count", 0)))
        if abs(measured_power - previous_value) > self._learning_stability_tolerance(previous_value):
            return self._restart_learning_sample(measured_power, now, current_phase_signature, current_voltage_signature)
        sample_count += 1
        learning_span = float(now) - float(learning_since)
        if sample_count >= self.LEARNED_POWER_STABLE_MIN_SAMPLES and learning_span >= self.LEARNED_POWER_STABLE_MIN_SECONDS:
            return self._apply_stable_learning(
                learned_power,
                updated_at=now,
                phase_signature=current_phase_signature,
                voltage_signature=learned_voltage_signature,
                signature_mismatch_sessions=0,
                checked_session_started_at=charging_started_at,
            )
        return bool(
            self._set_learning_tracking(
            self.service,
            state="learning",
            learned_power=learned_power,
            updated_at=now,
            learning_since=float(learning_since),
            sample_count=sample_count,
            phase_signature=current_phase_signature,
            voltage_signature=learned_voltage_signature,
            signature_mismatch_sessions=0,
            checked_session_started_at=None,
            )
        )

    def _apply_learning_sample(
        self,
        current_state: str,
        measured_power: float,
        current_phase_signature: str | None,
        current_voltage_signature: float | None,
        charging_started_at: float,
        now: float,
    ) -> bool:
        """Update learned power from one accepted sample inside the learning window."""
        previous = getattr(self.service, "learned_charge_power_watts", None)
        if self._should_restart_learning(current_state, previous, now):
            return self._restart_learning_sample(
                measured_power,
                now,
                current_phase_signature,
                current_voltage_signature,
            )

        assert previous is not None
        previous_value = float(previous)
        learned_power, learned_voltage_signature = self._smoothed_learning_values(
            previous_value,
            measured_power,
            current_voltage_signature,
        )
        if current_state == "stable":
            return self._apply_stable_learning(
                learned_power,
                updated_at=now,
                phase_signature=current_phase_signature,
                voltage_signature=learned_voltage_signature,
                signature_mismatch_sessions=0,
                checked_session_started_at=charging_started_at,
            )
        return self._apply_learning_progress(
            measured_power,
            previous_value,
            learned_power,
            learned_voltage_signature,
            current_phase_signature,
            current_voltage_signature,
            charging_started_at,
            now,
        )
