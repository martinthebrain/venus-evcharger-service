# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Pre-average and average-resolution helpers for Auto relay decisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .logic_types import NO_RELAY_DECISION, RelayDecisionState


class _AutoDecisionPreAverageMixin:
    def _pre_average_decision(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> tuple[RelayDecisionState, float | None]:
        svc = self.service
        early_decision = self._pre_average_gate_chain_result(svc, relay_on, grid_power, now, cached_inputs)
        if early_decision is not None:
            return early_decision
        if self._scheduled_night_charge_active(now):
            return NO_RELAY_DECISION, None
        battery_soc, early_decision = self._pre_average_battery_soc_result(
            battery_soc,
            relay_on,
            now,
            cached_inputs,
        )
        if early_decision is not None:
            return early_decision
        return self._pre_average_missing_input_result(
            relay_on,
            pv_power,
            battery_soc,
            grid_power,
            now,
            cached_inputs,
        )

    def _pre_average_gate_result(
        self,
        decision: RelayDecisionState,
    ) -> tuple[RelayDecisionState, float | None] | None:
        if self._resolved_auto_decision(decision) is None:
            return None
        return decision, None

    def _pre_average_gate_chain_result(
        self,
        svc: Any,
        relay_on: bool,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> tuple[RelayDecisionState, float | None] | None:
        decision_factories: tuple[Callable[[], RelayDecisionState], ...] = (
            lambda: self._pre_average_mode_decision(svc, relay_on, cached_inputs),
            lambda: self._pre_average_input_gate_decision(relay_on, grid_power, now, cached_inputs),
        )
        for decision_factory in decision_factories:
            early_decision = self._pre_average_gate_result(decision_factory())
            if early_decision is not None:
                return early_decision
        return None

    def _pre_average_battery_soc_result(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, tuple[RelayDecisionState, float | None] | None]:
        resolved_battery_soc, battery_soc_decision = self._resolved_battery_soc_decision(
            battery_soc,
            relay_on,
            now,
            cached_inputs,
        )
        return resolved_battery_soc, self._pre_average_gate_result(battery_soc_decision)

    def _pre_average_missing_input_result(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> tuple[RelayDecisionState, float | None]:
        if pv_power is not None and grid_power is not None:
            return NO_RELAY_DECISION, battery_soc
        assert battery_soc is not None
        return RelayDecisionState.resolved(
            self._handle_missing_inputs(relay_on, battery_soc, grid_power, now, cached_inputs)
        ), None

    def _pre_average_mode_decision(
        self,
        svc: Any,
        relay_on: bool,
        cached_inputs: bool,
    ) -> RelayDecisionState:
        if not self._mode_uses_auto_logic(svc.virtual_mode):
            return RelayDecisionState.resolved(self._handle_non_auto_mode(relay_on))
        if not bool(getattr(svc, "virtual_enable", 1)):
            return RelayDecisionState.resolved(self._handle_disabled_mode(cached_inputs))
        return self._decision_state(self._handle_cutover_pending(relay_on, cached_inputs))

    def _pre_average_input_gate_decision(
        self,
        relay_on: bool,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> RelayDecisionState:
        if self._scheduled_night_charge_active(now):
            return NO_RELAY_DECISION
        if not self._grid_recently_read(grid_power, now):
            return RelayDecisionState.resolved(self._handle_grid_missing(relay_on, now, cached_inputs))
        return self._decision_state(self._handle_grid_recovery_start_gate(relay_on, now, cached_inputs))

    def _resolved_battery_soc_decision(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, RelayDecisionState]:
        resolved_battery_soc, decision = self._resolve_battery_soc(battery_soc, relay_on, now, cached_inputs)
        return resolved_battery_soc, self._decision_state(decision)

    def _post_average_decision(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        now: float,
        cached_inputs: bool,
    ) -> tuple[RelayDecisionState, bool | None]:
        decision = self._handle_common_runtime_gates(relay_on, now, cached_inputs)
        if decision is not self._NO_DECISION:
            return self._decision_state(decision), None
        return NO_RELAY_DECISION, self.is_within_auto_daytime_window()

    def _decision_state(self, decision: bool | object) -> RelayDecisionState:
        if decision is self._NO_DECISION:
            return NO_RELAY_DECISION
        assert isinstance(decision, bool)
        return RelayDecisionState.resolved(decision)

    def _resolved_auto_decision(self, decision: RelayDecisionState) -> bool | None:
        if decision.is_pending:
            return None
        return decision.resolved_value()

    def _averaged_auto_metrics(
        self,
        now: float,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
        cached_inputs: bool,
    ) -> tuple[float, float] | None:
        avg_surplus_power, avg_grid_power = self._update_average_metrics(
            now,
            pv_power,
            grid_power,
            battery_soc,
            relay_on,
        )
        if avg_surplus_power is None or avg_grid_power is None:
            self.set_health("averaging", cached_inputs, relay_intent=relay_on)
            return None
        return avg_surplus_power, avg_grid_power

    def _decision_from_averages(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        decision, daytime_window_open = self._post_average_decision(
            relay_on,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            now,
            cached_inputs,
        )
        resolved = self._resolved_auto_decision(decision)
        if resolved is not None:
            return resolved
        assert daytime_window_open is not None
        if relay_on:
            return self._handle_relay_on(
                avg_surplus_power,
                avg_grid_power,
                battery_soc,
                daytime_window_open,
                now,
                cached_inputs,
            )
        return self._handle_relay_off(
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            daytime_window_open,
            now,
            cached_inputs,
        )

    def auto_decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
    ) -> bool:
        svc = self.service
        cached_inputs = bool(getattr(svc, "_auto_cached_inputs_used", False))
        now = self._learning_policy_now()
        pre_average_decision, battery_soc = self._pre_average_decision(
            relay_on,
            pv_power,
            battery_soc,
            grid_power,
            now,
            cached_inputs,
        )
        return self._auto_decision_after_pre_average(
            relay_on,
            pv_power,
            battery_soc,
            grid_power,
            now,
            cached_inputs,
            pre_average_decision,
        )

    def _auto_decision_after_pre_average(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
        pre_average_decision: RelayDecisionState,
    ) -> bool:
        resolved = self._resolved_auto_decision(pre_average_decision)
        if resolved is not None:
            return resolved
        if self._scheduled_night_charge_active(now):
            return self._scheduled_night_decision(relay_on, now, cached_inputs)
        pv_power, battery_soc, grid_power = self._required_average_inputs(pv_power, battery_soc, grid_power)
        averages = self._averaged_auto_metrics(
            now,
            pv_power,
            grid_power,
            battery_soc,
            relay_on,
            cached_inputs,
        )
        return self._decision_from_available_averages(
            relay_on,
            battery_soc,
            now,
            cached_inputs,
            averages,
        )

    @staticmethod
    def _required_average_inputs(
        pv_power: float | None,
        battery_soc: float | None,
        grid_power: float | None,
    ) -> tuple[float, float, float]:
        assert pv_power is not None
        assert battery_soc is not None
        assert grid_power is not None
        return pv_power, battery_soc, grid_power

    def _decision_from_available_averages(
        self,
        relay_on: bool,
        battery_soc: float,
        now: float,
        cached_inputs: bool,
        averages: tuple[float, float] | None,
    ) -> bool:
        if averages is None:
            return relay_on
        avg_surplus_power, avg_grid_power = averages
        return self._decision_from_averages(
            relay_on,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            now,
            cached_inputs,
        )
