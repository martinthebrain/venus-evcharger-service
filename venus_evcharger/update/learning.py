# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Venus EV charger service.

The update cycle is the heartbeat of the wallbox integration. Every pass reads
the latest Shelly snapshot, lets Auto mode decide whether the relay should be
on, applies corrections if needed, and then publishes the resulting charger
state back to Venus OS.
"""

from __future__ import annotations

from .learning_support import _UpdateCycleLearningSupport


class _UpdateCycleLearning(_UpdateCycleLearningSupport):
    def refresh_learned_charge_power_state(self, now: float) -> bool:
        """Refresh the coarse learned-power state outside active learning samples."""
        learned_power = self._stored_positive_learned_charge_power()
        if learned_power is None:
            return self._clear_learning_tracking()
        current_state = self._stored_learning_state()
        stored_phase_signature = self._stored_learning_phase_signature()
        current_phase_signature = self._current_learning_phase_signature()
        phase_reset = self._phase_change_reset(stored_phase_signature, current_phase_signature)
        if phase_reset is not None:
            return phase_reset
        return self._refresh_learning_tracking_without_phase_reset(
            now,
            learned_power,
            current_state,
            stored_phase_signature,
            current_phase_signature,
        )

    def _refresh_learning_tracking_without_phase_reset(
        self,
        now: float,
        learned_power: float,
        current_state: str,
        stored_phase_signature: str | None,
        current_phase_signature: str,
    ) -> bool:
        """Refresh learning state once the stored/current phase signatures already agree."""
        if self._is_learned_charge_power_stale(now):
            return self._apply_stale_learning(learned_power, stored_phase_signature)
        if self._should_restore_stable_learning(current_state):
            return self._restore_stable_learning(learned_power, stored_phase_signature, current_phase_signature)
        if current_state == "stale":
            return False
        return self._preserve_learning_tracking(
            learned_power,
            current_state,
            stored_phase_signature,
            current_phase_signature,
        )

    def reconcile_learned_charge_power_signature(
        self,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
        pm_confirmed: bool = True,
    ) -> bool:
        """Invalidate learned power after repeated session signatures diverge clearly."""
        learned_power = self._stable_learned_power()
        if not pm_confirmed or learned_power is None:
            return False

        stored_phase_signature = self._normalize_learned_charge_power_phase(
            getattr(self.service, "learned_charge_power_phase", None)
        )
        current_phase_signature = self._current_learning_phase_signature()
        phase_reset = self._phase_change_reset(stored_phase_signature, current_phase_signature)
        if phase_reset is not None:
            return phase_reset

        current_session_started_at = self._eligible_signature_session_started_at(relay_on, now)
        if current_session_started_at is None:
            return False

        mismatch_reasons, current_voltage_signature = self._signature_mismatch_reasons(
            power,
            voltage,
            learned_power,
        )
        return self._apply_signature_reconcile_result(
            learned_power,
            power,
            current_phase_signature,
            current_voltage_signature,
            current_session_started_at,
            mismatch_reasons,
        )

    def update_learned_charge_power(
        self,
        relay_on: bool,
        status: int,
        power: float,
        voltage: float,
        now: float,
        pm_confirmed: bool = True,
    ) -> bool:
        """Learn the real charging power from stable charging measurements.

        Learning is intentionally limited to an early full-load window so
        end-of-charge taper or transient spikes do not poison the baseline that
        later Auto-mode decisions reuse.
        """
        current_state = self._normalize_learned_charge_power_state(
            getattr(self.service, "learned_charge_power_state", "unknown")
        )
        current_phase_signature = self._current_learning_phase_signature()
        current_voltage_signature = self._current_learning_voltage_signature(voltage)
        charging_started_at, decision = self._learning_session_result(
            bool(getattr(self.service, "auto_learn_charge_power_enabled", True)),
            pm_confirmed,
            relay_on,
            status,
            now,
            current_state,
        )
        if decision is not None:
            return decision

        measured_power, sample_reason = self._accepted_learning_sample_result(power, voltage)
        if measured_power is None:
            self._apply_learning_diagnostics(
                self.service,
                confidence=0.0,
                stability_score=0.0,
                reason="sample-rejected",
                detail=sample_reason,
            )
            return False
        assert charging_started_at is not None
        return self._apply_learning_sample(
            current_state,
            measured_power,
            current_phase_signature,
            current_voltage_signature,
            charging_started_at,
            now,
        )

    def _resolved_learning_phase_signature(
        self,
        stored_phase_signature: str | None,
        current_phase_signature: str,
    ) -> str:
        """Return the phase signature that should remain stored."""
        return stored_phase_signature if stored_phase_signature is not None else current_phase_signature

    def _apply_stale_learning(self, learned_power: float, stored_phase_signature: str | None) -> bool:
        """Mark stored learning as stale while preserving its signatures."""
        svc = self.service
        voltage_signature, signature_mismatch_sessions, checked_session_started_at = self._learning_signature_context()
        return bool(
            self._set_learning_tracking(
                svc,
                state="stale",
                learned_power=learned_power,
                updated_at=getattr(svc, "learned_charge_power_updated_at", None),
                learning_since=None,
                sample_count=0,
                phase_signature=stored_phase_signature,
                voltage_signature=voltage_signature,
                signature_mismatch_sessions=signature_mismatch_sessions,
                checked_session_started_at=checked_session_started_at,
                confidence=0.0,
                stability_score=getattr(svc, "learned_charge_power_stability_score", 0.0),
                reason="learning-stale",
                detail="max-age",
            )
        )

    def _should_restore_stable_learning(self, current_state: str) -> bool:
        """Return True when a stored learned power should become stable again."""
        return current_state == "unknown" and getattr(self.service, "learned_charge_power_updated_at", None) is not None

    def _restore_stable_learning(
        self,
        learned_power: float,
        stored_phase_signature: str | None,
        current_phase_signature: str,
    ) -> bool:
        """Restore one stored learned power to the stable state."""
        voltage_signature, signature_mismatch_sessions, checked_session_started_at = self._learning_signature_context()
        return self._apply_stable_learning(
            learned_power,
            updated_at=getattr(self.service, "learned_charge_power_updated_at", None),
            phase_signature=self._resolved_learning_phase_signature(stored_phase_signature, current_phase_signature),
            voltage_signature=voltage_signature,
            signature_mismatch_sessions=signature_mismatch_sessions,
            checked_session_started_at=checked_session_started_at,
            confidence=1.0,
            stability_score=getattr(self.service, "learned_charge_power_stability_score", 1.0),
            reason="learning-restored",
            detail="stored-value",
        )

    def _preserve_learning_tracking(
        self,
        learned_power: float,
        current_state: str,
        stored_phase_signature: str | None,
        current_phase_signature: str,
    ) -> bool:
        """Keep the current learned-power tracking state unchanged."""
        svc = self.service
        voltage_signature, signature_mismatch_sessions, checked_session_started_at = self._learning_signature_context()
        return bool(
            self._set_learning_tracking(
                svc,
                state=current_state,
                learned_power=learned_power,
                updated_at=getattr(svc, "learned_charge_power_updated_at", None),
                learning_since=getattr(svc, "learned_charge_power_learning_since", None),
                sample_count=max(0, int(getattr(svc, "learned_charge_power_sample_count", 0))),
                phase_signature=self._resolved_learning_phase_signature(stored_phase_signature, current_phase_signature),
                voltage_signature=voltage_signature,
                signature_mismatch_sessions=signature_mismatch_sessions,
                checked_session_started_at=checked_session_started_at,
            )
        )
