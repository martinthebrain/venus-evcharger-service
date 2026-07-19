# SPDX-License-Identifier: GPL-3.0-or-later
"""Public composition root for Auto-mode decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from venus_evcharger.auto.component_context import AutoDecisionContext
from venus_evcharger.auto.logic_decisions import AutoRelayDecision
from venus_evcharger.auto.logic_decisions_preaverage import AutoDecisionWorkflow
from venus_evcharger.auto.logic_gates_battery_balance import AutoBatteryBalance
from venus_evcharger.auto.logic_gates_battery_balance_support import AutoBatteryBalancePolicy
from venus_evcharger.auto.logic_gates_battery_learning import AutoBatteryLearning
from venus_evcharger.auto.logic_gates_metrics import AutoDecisionMetrics
from venus_evcharger.auto.logic_gates_runtime import AutoRuntimeGates
from venus_evcharger.auto.logic_learning import AutoLearningPolicy
from venus_evcharger.auto.logic_samples import AutoSampleTracker
from venus_evcharger.ports.auto import AutoDecisionPort


class AutoDecisionController:
    """Compose focused Auto components behind the service-facing API."""

    def __init__(
        self,
        port: object,
        health_code_func: Callable[[str], int],
        mode_uses_auto_logic_func: Callable[[Any], bool],
    ) -> None:
        if not isinstance(port, AutoDecisionPort):
            raise TypeError("AutoDecisionController requires AutoDecisionPort")
        self.context = AutoDecisionContext(port, health_code_func, mode_uses_auto_logic_func)
        self.learning = AutoLearningPolicy(self.context)
        self.samples = AutoSampleTracker(self.context, self.learning)
        self.battery_learning = AutoBatteryLearning(self.context)
        self.battery_balance_policy = AutoBatteryBalancePolicy(self.context, self.battery_learning)
        self.battery_balance = AutoBatteryBalance(
            self.context,
            self.battery_learning,
            self.battery_balance_policy,
        )
        self.metrics = AutoDecisionMetrics(
            self.context,
            self.learning,
            self.samples,
            self.battery_learning,
            self.battery_balance,
        )
        self.gates = AutoRuntimeGates(self.context, self.learning, self.samples)
        self.relay = AutoRelayDecision(
            self.context,
            self.learning,
            self.samples,
            self.gates,
        )
        self.workflow = AutoDecisionWorkflow(
            self.context,
            self.learning,
            self.samples,
            self.metrics,
            self.gates,
            self.relay,
        )

    @staticmethod
    def get_available_surplus_watts(pv_power: float | int, grid_power: float | int) -> float:
        return AutoSampleTracker.get_available_surplus_watts(pv_power, grid_power)

    def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> None:
        self.samples.add_auto_sample(now, surplus_power, grid_power)

    def clear_auto_samples(self) -> None:
        self.samples.clear_auto_samples()

    def average_auto_metric(self, index: int) -> float | None:
        return self.samples.average_auto_metric(index)

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        self.samples.mark_relay_changed(relay_on, now)

    def is_within_auto_daytime_window(self, current_dt: datetime | None = None) -> bool:
        return self.samples.is_within_auto_daytime_window(current_dt)

    def set_health(self, reason: str, cached: bool = False, relay_intent: bool | None = None) -> None:
        self.samples.set_health(reason, cached, relay_intent)

    def auto_decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
    ) -> bool:
        return self.workflow.auto_decide_relay(relay_on, pv_power, battery_soc, grid_power)
