# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime cycle orchestration helpers for online update passes."""

from __future__ import annotations

import logging

from venus_evcharger.core.contracts import finite_float_or_none
from venus_evcharger.update.input_cache import InputCacheResolver
from venus_evcharger.update.learning import LearningController
from venus_evcharger.update.offline_publish import OfflinePublisher
from venus_evcharger.update.pm_snapshot import PmSnapshotResolver
from venus_evcharger.update.relay import RelayComponents
from venus_evcharger.update.runtime_cycle_contracts import UpdateCycleServicePort
from venus_evcharger.update.runtime_cycle_warnings import (
    blocking_charger_health_warning_spec,
    switch_feedback_warning_spec,
)
from venus_evcharger.update.state import UpdateStateController
from venus_evcharger.update.victron_ess_balance import VictronEssBalanceController


class RuntimeCycleCoordinator:
    """Coordinate one update pass through explicit policy and I/O components."""

    def __init__(
        self,
        service: UpdateCycleServicePort,
        state: UpdateStateController,
        pm_snapshots: PmSnapshotResolver,
        inputs: InputCacheResolver,
        offline: OfflinePublisher,
        relay: RelayComponents,
        learning: LearningController,
        victron_ess_balance: VictronEssBalanceController,
    ) -> None:
        self.service = service
        self._state = state
        self._pm_snapshots = pm_snapshots
        self._inputs = inputs
        self._offline = offline
        self._relay = relay
        self._learning = learning
        self._victron_ess_balance = victron_ess_balance

    def complete_update_cycle(
        self,
        changed: bool,
        now: float,
        relay_on: bool,
        power: float,
        current: float,
        status: int,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> None:
        """Finalize a successful update cycle and log the current state."""
        svc = self.service
        if changed:
            svc.state.bump_update_index(now)
        completed_at = svc.time_now()
        svc._last_successful_update_at = completed_at
        svc._last_recovery_attempt_at = None
        svc.last_update = completed_at
        svc.state.publish_companion_bridge(completed_at)
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

    def run(self) -> bool:
        """Execute one full update cycle and report whether the loop should continue."""
        svc = self.service
        now = svc.time_now()
        worker_snapshot = self._state.prepare_update_cycle(svc, now)
        pm_status = self._pm_snapshots.resolve_pm_status_for_update(svc, worker_snapshot, now)
        if pm_status is None:
            return self._offline.publish_offline_update(now)
        self._run_online_update_cycle(pm_status, worker_snapshot, now)
        return True

    def _run_online_update_cycle(
        self,
        pm_status: dict[str, object],
        worker_snapshot: dict[str, object],
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
        pv_power, battery_soc, grid_power = self._inputs.resolve_auto_inputs(
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
        self._victron_ess_balance.apply_victron_ess_balance_bias(
            self.service,
            now,
            auto_mode_active,
        )
        relay_on, power, current, relay_confirmed = self._relay.status.apply_relay_decision(
            self.service,
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
        changed = self._relay.status.publish_online_update(
            self.service,
            pm_status,
            status,
            energy_forward,
            relay_on,
            power,
            voltage,
            now,
        )
        learning_updated = self._learning.update_learned_charge_power(
            relay_on,
            status,
            effective_power,
            voltage,
            now,
            pm_confirmed=relay_confirmed,
        )
        if learning_state_changed or learning_updated:
            self._state.save_runtime_state_best_effort("learning-state")
        self.complete_update_cycle(
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
        pm_status: dict[str, object],
        relay_on: bool,
        power: float,
        voltage: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> tuple[bool, float, float, bool, bool, str | None]:
        """Return relay-state context plus the desired relay target for this cycle."""
        svc = self.service
        relay_on, power, current, pm_confirmed, phase_switch_override = (
            self._relay.foundation.phase_switch.orchestrate_pending_phase_switch(
                svc,
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
        phase_override = self._relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            svc,
            desired_relay,
            relay_on,
            voltage,
            now,
            auto_mode_active,
        )
        if phase_override is not None:
            desired_relay = bool(phase_override)
        self._relay.foundation.targets.apply_current_target(svc, desired_relay, now, auto_mode_active)
        return relay_on, power, current, pm_confirmed, desired_relay, (
            switch_health or charger_health
        )

    @staticmethod
    def _desired_relay_target(
        svc: UpdateCycleServicePort,
        relay_on: bool,
        phase_switch_override: bool | None,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> bool:
        """Return the desired relay state before charger-health overrides are applied."""
        if phase_switch_override is not None:
            return bool(phase_switch_override)
        return bool(svc.auto.decide_relay(relay_on, pv_power, battery_soc, grid_power))

    def _blocking_charger_health(
        self,
        desired_relay: bool,
        relay_on: bool,
        now: float,
    ) -> str | None:
        """Return a charger-health override and emit one warning when it blocks charging."""
        svc = self.service
        charger_health = self._relay.foundation.health.charger_health_override(svc, now)
        if charger_health is None:
            return None
        charging_requested = bool(desired_relay) or bool(relay_on)
        if charging_requested:
            charger_readback = svc._readback_resolver.resolve(now).charger
            warning_key, message, args = blocking_charger_health_warning_spec(
                svc,
                charger_health,
                None if charger_readback is None else charger_readback.state,
            )
            svc.runtime.warning_throttled(
                warning_key,
                svc.auto_shelly_soft_fail_seconds,
                message,
                *args,
            )
        return charger_health

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
        switch_health = self._relay.foundation.health.switch_feedback_health_override(
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
        readbacks = svc._readback_resolver.resolve(now)
        warning_key, warning_text, warning_args = switch_feedback_warning_spec(
            switch_health,
            desired_relay,
            relay_on,
            power,
            current,
            svc,
            None if readbacks.charger is None else readbacks.charger.state,
            None if readbacks.switch is None else readbacks.switch.state,
        )
        svc.runtime.warning_throttled(
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
        readback = svc._readback_resolver.resolve(now).charger
        charger_power = None if readback is None else finite_float_or_none(readback.state.power_w)
        effective_power = power if charger_power is None else max(0.0, charger_power)
        status = self._relay.status.derive_status_code(
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
            self.service.auto.set_health(charger_health, cached=False)

    def _prepared_online_update_state(
        self,
        pm_status: dict[str, object],
        now: float,
    ) -> tuple[dict[str, object], bool, float, float, float, float, bool, bool]:
        """Return normalized online-update state after startup target handling."""
        svc = self.service
        relay_on, power, voltage, current, energy_forward = self._inputs.extract_pm_measurements(
            svc,
            pm_status,
        )
        pm_status = self._state.apply_startup_manual_target(pm_status, now)
        relay_on, power, voltage, current, energy_forward = self._inputs.extract_pm_measurements(
            svc,
            pm_status,
        )
        pm_confirmed = self._relay.foundation.telemetry.pm_status_confirmed(pm_status)
        if voltage > 0.0:
            svc._last_voltage = voltage
        auto_mode_active = svc.auto.mode_uses_auto_logic(svc.virtual_mode)
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
        learning_state_changed = self._learning.refresh_learned_charge_power_state(now)
        learning_state_changed |= self._learning.reconcile_learned_charge_power_signature(
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
        relay_sync_health = self._relay.foundation.telemetry.relay_sync_health_override(
            self.service,
            relay_on,
            relay_confirmed,
            now,
        )
        if relay_sync_health is not None:
            self.service.auto.set_health(relay_sync_health, cached=False)
        return relay_sync_health


__all__ = ["RuntimeCycleCoordinator"]
