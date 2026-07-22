# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from typing import Any

from venus_evcharger.runtime.contracts import AgeSeconds, HealthCode
from venus_evcharger.runtime.support import RuntimeSupportController as ProductionRuntimeSupportController


class _RuntimeAutoRole:
    def __init__(self, service: Any) -> None:
        self.service = service

    def handle_command(self, command: object) -> Any:
        return self.service.handle_control_command(command)

    def invalidate_pv_services(self) -> None:
        self.service._invalidate_auto_pv_services()

    def invalidate_battery_service(self) -> None:
        self.service._invalidate_auto_battery_service()


class _RuntimeUpdateRole:
    def __init__(self, service: Any) -> None:
        self.service = service

    def update(self) -> bool:
        return bool(self.service._update())


class _RuntimeStateRole:
    def __init__(self, service: Any) -> None:
        self.service = service

    def summary(self) -> str:
        return str(self.service._state_summary())


class RuntimeSupportController(ProductionRuntimeSupportController):
    """Compose the production runtime controller with explicit test roles."""

    def __init__(self, service: Any, age_seconds_func: AgeSeconds, health_code_func: HealthCode) -> None:
        super().__init__(service, age_seconds_func, health_code_func)
        service.runtime = self
        if not hasattr(service, "auto"):
            service.auto = _RuntimeAutoRole(service)
        if not hasattr(service, "update"):
            service.update = _RuntimeUpdateRole(service)
        if not hasattr(service, "state"):
            service.state = _RuntimeStateRole(service)


class RuntimeSupportTestCaseBase(unittest.TestCase):
    @staticmethod
    def _age_zero(_captured_at: float | int | None, _now: float | int | None) -> int:
        return 0

    @staticmethod
    def _age_five(_captured_at: float | int | None, _now: float | int | None) -> int:
        return 5

    @staticmethod
    def _health_zero(_reason: str) -> int:
        return 0

    @staticmethod
    def _health_nine(_reason: str) -> int:
        return 9

    @staticmethod
    def _health_ten(_reason: str) -> int:
        return 10

    @staticmethod
    def _never_stale(_now: float) -> bool:
        return False

    @staticmethod
    def _always_stale(_now: float) -> bool:
        return True


__all__ = ["RuntimeSupportController", "RuntimeSupportTestCaseBase", "SimpleNamespace"]
