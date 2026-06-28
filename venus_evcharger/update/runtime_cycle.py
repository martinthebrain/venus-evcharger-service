# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime cycle orchestration helpers for online update passes."""

from __future__ import annotations

import logging
from typing import Any

from .runtime_cycle_contracts import _UpdateCycleRuntimeContractsMixin


class _UpdateCycleRuntimeMixin(_UpdateCycleRuntimeContractsMixin):
    @staticmethod
    def complete_update_cycle(
        svc: Any,
        changed: bool,
        now: float,
        relay_on: bool,
        power: float,
        current: float,
        status: int,
        pv_power: Any,
        battery_soc: Any,
        grid_power: Any,
    ) -> None:
        """Finalize a successful update cycle and log the current state."""
        if changed:
            svc._bump_update_index(now)
        completed_at = svc._time_now()
        svc._last_successful_update_at = completed_at
        svc._last_recovery_attempt_at = None
        svc.last_update = completed_at
        publish_companion = getattr(svc, "_publish_companion_dbus_bridge", None)
        if callable(publish_companion):
            publish_companion(completed_at)
        logging.debug(
            "Wallbox relay=%s power=%sW current=%sA status=%s pv=%sW soc=%s%% grid=%sW mode=%s",
            relay_on,
            power,
            current,
            status,
            pv_power,
            battery_soc,
            grid_power,
            svc.virtual_mode,
        )

    def _run_update_cycle(self) -> bool:
        """Execute one full update cycle and report whether the loop should continue."""
        svc = self.service
        now = svc._time_now()
        worker_snapshot = self.prepare_update_cycle(svc, now)
        pm_status = self.resolve_pm_status_for_update(svc, worker_snapshot, now)
        if pm_status is None:
            return self.publish_offline_update(now)
        self._run_online_update_cycle(pm_status, worker_snapshot, now)
        return True

    def _run_online_update_cycle(
        self,
        pm_status: dict[str, Any],
        worker_snapshot: dict[str, Any],
        now: float,
    ) -> None:
        """Execute the online portion of one update cycle."""
        (
            pm_status,
            relay_on,
            power,
            voltage,
            current,
            energy_forward,
            pm_confirmed,
            auto_mode_active,
        ) = self._prepared_online_update_state(pm_status, now)
        learning_state_changed = self._refresh_learning_before_decision(
            relay_on,
            power,
            voltage,
            now,
            pm_confirmed,
        )
        pv_power, battery_soc, grid_power = self.resolve_auto_inputs(
            worker_snapshot,
            now,
            auto_mode_active,
        )
        relay_on, power, current, pm_confirmed, desired_relay, charger_health = (
            self._resolved_relay_decision(
                pm_status,
                relay_on,
                power,
                voltage,
                current,
                pm_confirmed,
                now,
                auto_mode_active,
                pv_power,
                battery_soc,
                grid_power,
            )
        )
        self.apply_victron_ess_balance_bias(self.service, now, auto_mode_active)
        relay_on, power, current, relay_confirmed = self.apply_relay_decision(
            desired_relay,
            relay_on,
            pm_status,
            power,
            current,
            now,
            auto_mode_active,
        )
        effective_power, status = self._status_after_relay_decision(
            relay_on,
            power,
            auto_mode_active,
            charger_health,
            now,
        )
        self._apply_post_decision_health(relay_on, relay_confirmed, now, charger_health)
        changed = self.publish_online_update(
            pm_status,
            status,
            energy_forward,
            relay_on,
            power,
            voltage,
            now,
        )
        learning_updated = self.update_learned_charge_power(
            relay_on,
            status,
            effective_power,
            voltage,
            now,
            pm_confirmed=relay_confirmed,
        )
        if learning_state_changed or learning_updated:
            self.save_runtime_state_best_effort("learning-state")
        self.complete_update_cycle(
            self.service,
            changed,
            now,
            relay_on,
            power,
            current,
            status,
            pv_power,
            battery_soc,
            grid_power,
        )

    def _resolved_relay_decision(
        self,
        pm_status: dict[str, Any],
        relay_on: bool,
        power: float,
        voltage: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
        pv_power: Any,
        battery_soc: Any,
        grid_power: Any,
    ) -> tuple[bool, float, float, bool, bool, str | None]:
        """Return relay-state context plus the desired relay target for this cycle."""
        svc = self.service
        relay_on, power, current, pm_confirmed, phase_switch_override = (
            self.orchestrate_pending_phase_switch(
                pm_status,
                relay_on,
                power,
                current,
                pm_confirmed,
                now,
                auto_mode_active,
            )
        )
        desired_relay = self._desired_relay_target(
            svc,
            relay_on,
            phase_switch_override,
            pv_power,
            battery_soc,
            grid_power,
        )
        switch_health = self._blocking_switch_feedback_health(
            desired_relay,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
        )
        if switch_health is not None:
            desired_relay = False
        charger_health = self._blocking_charger_health(desired_relay, relay_on, now)
        if charger_health is not None:
            desired_relay = False
        phase_override = self.maybe_apply_auto_phase_selection(
            svc,
            desired_relay,
            relay_on,
            voltage,
            now,
            auto_mode_active,
        )
        if phase_override is not None:
            desired_relay = bool(phase_override)
        self.apply_charger_current_target(svc, desired_relay, now, auto_mode_active)
        return relay_on, power, current, pm_confirmed, desired_relay, (
            switch_health or charger_health
        )

    @staticmethod
    def _desired_relay_target(
        svc: Any,
        relay_on: bool,
        phase_switch_override: bool | None,
        pv_power: Any,
        battery_soc: Any,
        grid_power: Any,
    ) -> bool:
        """Return the desired relay state before charger-health overrides are applied."""
        if phase_switch_override is not None:
            return bool(phase_switch_override)
        return bool(svc._auto_decide_relay(relay_on, pv_power, battery_soc, grid_power))

    def _blocking_charger_health(
        self,
        desired_relay: bool,
        relay_on: bool,
        now: float,
    ) -> str | None:
        """Return a charger-health override and emit one warning when it blocks charging."""
        svc = self.service
        charger_health = self.charger_health_override(svc, now)
        if charger_health is None:
            return None
        charging_requested = bool(desired_relay) or bool(relay_on)
        if charging_requested:
            warning_key, message, args = self._blocking_charger_health_warning_spec(
                svc,
                charger_health,
            )
            svc._warning_throttled(
                warning_key,
                svc.auto_shelly_soft_fail_seconds,
                message,
                *args,
            )
        return charger_health

    @staticmethod
    def _blocking_charger_health_warning_spec(
        svc: Any,
        charger_health: str,
    ) -> tuple[str, str, tuple[Any, ...]]:
        """Return warning metadata for one blocking charger-health reason."""
        if charger_health.startswith("charger-transport-"):
            return (
                "charger-transport-blocking",
                "Native charger transport override %s blocks charging (source=%s detail=%s)",
                (
                    charger_health,
                    getattr(svc, "_last_charger_transport_source", None),
                    getattr(svc, "_last_charger_transport_detail", None),
                ),
            )
        return (
            "charger-health-blocking",
            "Native charger health override %s blocks charging (status=%s fault=%s)",
            (
                charger_health,
                getattr(svc, "_last_charger_state_status", None),
                getattr(svc, "_last_charger_state_fault", None),
            ),
        )

    @staticmethod
    def _switch_feedback_warning_spec(
        switch_health: str,
        desired_relay: bool,
        relay_on: bool,
        power: float,
        current: float,
        svc: Any,
    ) -> tuple[str, str, tuple[Any, ...]]:
        """Return warning metadata for one blocking switch-feedback health reason."""
        specs: dict[str, tuple[str, str, tuple[Any, ...]]] = {
            "contactor-interlock": (
                "switch-interlock-blocking",
                "Switch interlock blocks charging (desired=%s relay=%s interlock_ok=%s)",
                (int(bool(desired_relay)), int(bool(relay_on)), getattr(svc, "_last_switch_interlock_ok", None)),
            ),
            "contactor-suspected-open": (
                "switch-suspected-open-blocking",
                "Contactor heuristics suspect OPEN state (relay=%s power=%.1f current=%.1f charger_status=%s)",
                (
                    int(bool(relay_on)),
                    float(power),
                    float(current),
                    getattr(svc, "_last_charger_state_status", None),
                ),
            ),
            "contactor-suspected-welded": (
                "switch-suspected-welded-blocking",
                "Contactor heuristics suspect WELDED state (relay=%s power=%.1f current=%.1f)",
                (int(bool(relay_on)), float(power), float(current)),
            ),
            "contactor-lockout-open": (
                "switch-lockout-open-blocking",
                "Latched contactor OPEN lockout blocks charging (count=%s source=%s)",
                (
                    int(getattr(svc, "_contactor_fault_counts", {}).get("contactor-suspected-open", 0)),
                    getattr(svc, "_contactor_lockout_source", ""),
                ),
            ),
            "contactor-lockout-welded": (
                "switch-lockout-welded-blocking",
                "Latched contactor WELDED lockout blocks charging (count=%s source=%s)",
                (
                    int(getattr(svc, "_contactor_fault_counts", {}).get("contactor-suspected-welded", 0)),
                    getattr(svc, "_contactor_lockout_source", ""),
                ),
            ),
        }
        return specs.get(
            switch_health,
            (
                "switch-feedback-blocking",
                "Switch feedback mismatch blocks charging (relay=%s feedback_closed=%s)",
                (int(bool(relay_on)), getattr(svc, "_last_switch_feedback_closed", None)),
            ),
        )

    def _blocking_switch_feedback_health(
        self,
        desired_relay: bool,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
    ) -> str | None:
        """Return one switch-feedback override and emit one warning when it blocks charging."""
        svc = self.service
        switch_health = self.switch_feedback_health_override(
            svc,
            desired_relay,
            relay_on,
            now,
            power=power,
            current=current,
            pm_confirmed=pm_confirmed,
        )
        if switch_health is None:
            return None
        warning_key, warning_text, warning_args = self._switch_feedback_warning_spec(
            switch_health,
            desired_relay,
            relay_on,
            power,
            current,
            svc,
        )
        svc._warning_throttled(
            warning_key,
            svc.auto_shelly_soft_fail_seconds,
            warning_text,
            *warning_args,
        )
        return switch_health

    def _status_after_relay_decision(
        self,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        health_reason: str | None,
        now: float,
    ) -> tuple[float, int]:
        """Return effective power and derived Venus status after relay application."""
        svc = self.service
        effective_power = self._fresh_charger_power_readback(svc, now)
        if effective_power is None:
            effective_power = power
        status = self.derive_status_code(
            svc,
            relay_on,
            effective_power,
            auto_mode_active,
            health_reason=health_reason,
            now=now,
        )
        return effective_power, status

    def _apply_post_decision_health(
        self,
        relay_on: bool,
        relay_confirmed: bool,
        now: float,
        charger_health: str | None,
    ) -> None:
        """Apply relay-sync or charger-derived health after one relay decision."""
        relay_sync_health = self._apply_relay_sync_health(relay_on, relay_confirmed, now)
        if relay_sync_health is None and charger_health is not None:
            self.service._set_health(charger_health, cached=False)

    def _prepared_online_update_state(
        self,
        pm_status: dict[str, Any],
        now: float,
    ) -> tuple[dict[str, Any], bool, float, float, float, float, bool, bool]:
        """Return normalized online-update state after startup target handling."""
        svc = self.service
        relay_on, power, voltage, current, energy_forward = self.extract_pm_measurements(
            svc,
            pm_status,
        )
        pm_status = self.apply_startup_manual_target(pm_status, now)
        relay_on, power, voltage, current, energy_forward = self.extract_pm_measurements(
            svc,
            pm_status,
        )
        pm_confirmed = self._pm_status_confirmed(pm_status)
        if voltage > 0.0:
            svc._last_voltage = voltage
        auto_mode_active = svc._mode_uses_auto_logic(svc.virtual_mode)
        return (
            pm_status,
            relay_on,
            power,
            voltage,
            current,
            energy_forward,
            pm_confirmed,
            auto_mode_active,
        )

    def _refresh_learning_before_decision(
        self,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
        pm_confirmed: bool,
    ) -> bool:
        """Refresh learned-power state before Auto decides on relay changes."""
        learning_state_changed = self.refresh_learned_charge_power_state(now)
        learning_state_changed |= self.reconcile_learned_charge_power_signature(
            relay_on,
            power,
            voltage,
            now,
            pm_confirmed=pm_confirmed,
        )
        return bool(learning_state_changed)

    def _apply_relay_sync_health(
        self,
        relay_on: bool,
        relay_confirmed: bool,
        now: float,
    ) -> str | None:
        """Publish one relay-sync health override when needed and return the applied reason."""
        relay_sync_health = self.relay_sync_health_override(
            relay_on,
            relay_confirmed,
            now,
        )
        if relay_sync_health is not None:
            self.service._set_health(relay_sync_health, cached=False)
        return relay_sync_health
