# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Auto-mode controller facade for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from venus_evcharger.auto.workflow import AutoDecisionWorkflowMixin
from venus_evcharger.auto.logic_types import NO_RELAY_DECISION


class AutoDecisionController(AutoDecisionWorkflowMixin):
    """Thin public facade for the internal Auto-mode decision workflow."""

    _NO_DECISION = NO_RELAY_DECISION

    def __init__(
        self,
        service: Any,
        health_code_func: Callable[[str], int],
        mode_uses_auto_logic_func: Callable[[Any], bool],
    ) -> None:
        self.service = service
        if hasattr(service, "bind_controller"):
            service.bind_controller(self)
        self._health_code = health_code_func
        self._mode_uses_auto_logic = mode_uses_auto_logic_func

    def _service_method(self, public_name: str, legacy_name: str) -> Any:
        legacy_override = self._bound_port_override(legacy_name)
        if legacy_override is not None:
            return legacy_override
        public_method = self._external_service_method(public_name)
        if public_method is not None:
            return public_method
        return self._external_service_method(legacy_name)

    def _bound_port_override(self, legacy_name: str) -> Any:
        target = getattr(self.service, "_service", None)
        if target is None:
            return None
        method = getattr(target, "__dict__", {}).get(legacy_name)
        return method if callable(method) else None

    def _external_service_method(self, name: str) -> Any:
        if not name.startswith("_") and getattr(self.service, "_controller", None) is self:
            return None
        method = getattr(self.service, name, None)
        if not callable(method):
            return None
        if getattr(method, "__self__", None) is self:
            return None
        return method

    def available_surplus_watts(self, pv_power: float | int, grid_power: float | int) -> float:
        method = self._service_method("get_available_surplus_watts", "_get_available_surplus_watts")
        if callable(method):
            return float(method(pv_power, grid_power))
        return super().get_available_surplus_watts(pv_power, grid_power)

    def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> None:
        method = self._service_method("add_auto_sample", "_add_auto_sample")
        if callable(method):
            method(now, surplus_power, grid_power)
            return
        super().add_auto_sample(now, surplus_power, grid_power)

    def average_auto_metric(self, index: int) -> float | None:
        method = self._service_method("average_auto_metric", "_average_auto_metric")
        if callable(method):
            value = method(index)
            return None if value is None else float(value)
        return super().average_auto_metric(index)

    def is_within_auto_daytime_window(self, current_dt: Any = None) -> bool:
        method = self._service_method("is_within_auto_daytime_window", "_is_within_auto_daytime_window")
        if callable(method) and current_dt is None:
            return bool(method())
        return super().is_within_auto_daytime_window(current_dt)

    def save_runtime_state(self) -> Any:
        save = getattr(self.service, "save_runtime_state", None)
        if callable(save):
            return save()
        return self.service._save_runtime_state()

    def peek_pending_relay_command(self) -> Any:
        peek = getattr(self.service, "peek_pending_relay_command", None)
        if callable(peek):
            return peek()
        return self.service._peek_pending_relay_command()

    def write_auto_audit_event(self, *args: Any, **kwargs: Any) -> Any:
        write = getattr(self.service, "write_auto_audit_event", None)
        if callable(write):
            return write(*args, **kwargs)
        return self.service._write_auto_audit_event(*args, **kwargs)
