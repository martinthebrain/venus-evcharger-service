# SPDX-License-Identifier: GPL-3.0-or-later
"""Pre-average and average-resolution helpers for Auto relay decisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .component_context import AutoDecisionContext
from .logic_types import NO_RELAY_DECISION, RelayDecision, RelayDecisionState


class AutoWorkflowLearning(Protocol):
    def learning_policy_now(self) -> float: ...


class AutoWorkflowSamples(Protocol):
    def scheduled_night_charge_active(self, now: float | None = None) -> bool: ...

    def is_within_auto_daytime_window(self) -> bool: ...

    def set_health(self, reason: str, cached: bool, relay_intent: bool | None = None) -> None: ...


class AutoWorkflowMetrics(Protocol):
    def update_average_metrics(
        self,
        now: float,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
    ) -> tuple[float | None, float | None]: ...


class AutoWorkflowGates(Protocol):
    def handle_non_auto_mode(self, relay_on: bool) -> bool: ...

    def handle_disabled_mode(self, cached_inputs: bool) -> bool: ...

    def handle_cutover_pending(self, relay_on: bool, cached_inputs: bool) -> RelayDecision: ...

    def grid_recently_read(self, grid_power: float | None, now: float) -> bool: ...

    def handle_grid_missing(self, relay_on: bool, now: float, cached_inputs: bool) -> bool: ...

    def handle_grid_recovery_start_gate(
        self,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> RelayDecision: ...

    def resolve_battery_soc(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, RelayDecision]: ...

    def handle_missing_inputs(
        self,
        relay_on: bool,
        battery_soc: float,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> bool: ...

    def handle_common_runtime_gates(self, relay_on: bool, now: float, cached_inputs: bool) -> RelayDecision: ...


class AutoWorkflowRelay(Protocol):
    def handle_relay_on(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool: ...

    def handle_relay_off(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool: ...

    def scheduled_night_decision(self, relay_on: bool, now: float, cached_inputs: bool) -> bool: ...


class AutoDecisionWorkflow:
    """Coordinate composed Auto components into one relay decision workflow."""

    def __init__(
        self,
        context: AutoDecisionContext,
        learning: AutoWorkflowLearning,
        samples: AutoWorkflowSamples,
        metrics: AutoWorkflowMetrics,
        gates: AutoWorkflowGates,
        relay: AutoWorkflowRelay,
    ) -> None:
        self._context = context
        self.service = context.service
        self.learning = learning
        self.samples = samples
        self.metrics = metrics
        self.gates = gates
        self.relay = relay

    def _pre_average_decision(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> tuple[RelayDecisionState, float | None]:
        early_decision = self._pre_average_gate_chain_result(relay_on, grid_power, now, cached_inputs)
        if early_decision is not None:
            return early_decision
        if self.samples.scheduled_night_charge_active(now):
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
        relay_on: bool,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> tuple[RelayDecisionState, float | None] | None:
        decision_factories: tuple[Callable[[], RelayDecisionState], ...] = (
            lambda: self._pre_average_mode_decision(relay_on, cached_inputs),
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
            self.gates.handle_missing_inputs(relay_on, battery_soc, grid_power, now, cached_inputs)
        ), None

    def _pre_average_mode_decision(
        self,
        relay_on: bool,
        cached_inputs: bool,
    ) -> RelayDecisionState:
        if not self._context.mode_uses_auto_logic(self._context.port.mode()):
            return RelayDecisionState.resolved(self.gates.handle_non_auto_mode(relay_on))
        if not self._context.port.controller_enabled():
            return RelayDecisionState.resolved(self.gates.handle_disabled_mode(cached_inputs))
        return self._decision_state(self.gates.handle_cutover_pending(relay_on, cached_inputs))

    def _pre_average_input_gate_decision(
        self,
        relay_on: bool,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> RelayDecisionState:
        if self.samples.scheduled_night_charge_active(now):
            return NO_RELAY_DECISION
        if not self.gates.grid_recently_read(grid_power, now):
            return RelayDecisionState.resolved(self.gates.handle_grid_missing(relay_on, now, cached_inputs))
        return self._decision_state(self.gates.handle_grid_recovery_start_gate(relay_on, now, cached_inputs))

    def _resolved_battery_soc_decision(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, RelayDecisionState]:
        resolved_battery_soc, decision = self.gates.resolve_battery_soc(
            battery_soc, relay_on, now, cached_inputs
        )
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
        decision = self.gates.handle_common_runtime_gates(relay_on, now, cached_inputs)
        if decision is not NO_RELAY_DECISION:
            return self._decision_state(decision), None
        return NO_RELAY_DECISION, self.samples.is_within_auto_daytime_window()

    def _decision_state(self, decision: RelayDecision) -> RelayDecisionState:
        if isinstance(decision, RelayDecisionState):
            return decision
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
        avg_surplus_power, avg_grid_power = self.metrics.update_average_metrics(
            now,
            pv_power,
            grid_power,
            battery_soc,
            relay_on,
        )
        if avg_surplus_power is None or avg_grid_power is None:
            self.samples.set_health("averaging", cached_inputs, relay_intent=relay_on)
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
            return self.relay.handle_relay_on(
                avg_surplus_power,
                avg_grid_power,
                battery_soc,
                daytime_window_open,
                now,
                cached_inputs,
            )
        return self.relay.handle_relay_off(
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
        cached_inputs = bool(svc._auto_cached_inputs_used) if hasattr(svc, "_auto_cached_inputs_used") else False
        now = self.learning.learning_policy_now()
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
        if self.samples.scheduled_night_charge_active(now):
            return self.relay.scheduled_night_decision(relay_on, now, cached_inputs)
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
