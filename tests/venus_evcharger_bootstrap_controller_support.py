# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.app.bootstrap_support import logging_level_from_config as _logging_level_from_config
from venus_evcharger.bootstrap.controller import (
    ServiceBootstrapController,
    _enable_fault_diagnostics,
    _install_signal_logging,
    _request_mainloop_quit,
    _run_service_loop,
    run_service_main,
)
from venus_evcharger.bootstrap.config_shared import MONTH_WINDOW_DEFAULTS, _seasonal_month_windows
from venus_evcharger.bootstrap.contracts import MonthWindow
from venus_evcharger.ports.gateway_publication import (
    CompanionServiceIdentity,
    EvcsServiceIdentity,
    PublicationPriority,
    PublicationReceipt,
)
from venus_evcharger.service.controller_owner import ServiceControllerOwner, ServiceFunctionBundle


class _FakeGobjectTimers:
    def __init__(self) -> None:
        self.timeout_calls: list[tuple[int, object]] = []

    def timeout_add(self, interval: int, callback: object) -> object:
        call = (interval, callback)
        self.timeout_calls.append(call)
        return call


class _RecordingGatewayPublication:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.evcs_registrations: list[tuple[EvcsServiceIdentity, dict[str, object]]] = []

    def register_evcs(
        self,
        identity: EvcsServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        self.evcs_registrations.append((identity, dict(initial_fields)))
        return PublicationReceipt(self.accepted, "bootstrap-registration")

    def publish_evcs_fields(
        self,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del fields, priority
        return PublicationReceipt(self.accepted, "bootstrap-publish")

    def register_companion(
        self,
        identity: CompanionServiceIdentity,
        initial_fields: Mapping[str, object],
    ) -> PublicationReceipt:
        del identity, initial_fields
        return PublicationReceipt(self.accepted, "bootstrap-companion-registration")

    def publish_companion_fields(
        self,
        service_id: str,
        fields: Mapping[str, object],
        *,
        priority: PublicationPriority,
    ) -> PublicationReceipt:
        del service_id, fields, priority
        return PublicationReceipt(self.accepted, "bootstrap-companion-publish")


def _phase_values(
    power: float | int,
    voltage: float | int,
    phase: str,
    voltage_mode: str,
) -> dict[str, dict[str, float]]:
    del voltage_mode
    return {phase: {"power": float(power), "voltage": float(voltage)}}


def _normalize_phase(value: object) -> str:
    return str(value)


def _normalize_mode(value: object) -> int:
    return int(str(value))


def _mode_uses_auto_logic(mode: object) -> bool:
    return _normalize_mode(mode) in (1, 2)


def _month_window(
    config: configparser.ConfigParser,
    month: int,
    start: str,
    end: str,
) -> object:
    del config, month, start, end
    return ((8, 0), (18, 0))


def _age_seconds(updated_at: float | int | None, now: float | int | None) -> int:
    return int(float(now or 0) - float(updated_at or 0))


def _health_code(reason: str) -> int:
    return len(reason)


def _read_version(_name: str) -> str:
    return "1.0"


class ServiceBootstrapControllerTestCase(unittest.TestCase):
    @staticmethod
    def _function_bundle(
        *,
        normalize_phase: Callable[[object], str] = _normalize_phase,
        normalize_mode: Callable[[object], int] = _normalize_mode,
        mode_uses_auto_logic: Callable[[object], bool] = _mode_uses_auto_logic,
        month_window: MonthWindow = _month_window,
        read_version: Callable[[str], str] = _read_version,
        gobject: object | None = None,
        script_path: str = "/tmp/venus_evcharger_service.py",
    ) -> ServiceFunctionBundle:
        return ServiceFunctionBundle(
            normalize_phase=normalize_phase,
            normalize_mode=normalize_mode,
            mode_uses_auto_logic=mode_uses_auto_logic,
            month_window=month_window,
            age_seconds=_age_seconds,
            health_code=_health_code,
            phase_values=_phase_values,
            read_version=read_version,
            gobject=gobject if gobject is not None else _FakeGobjectTimers(),
            script_path=script_path,
            config_path="/tmp/venus-evcharger.ini",
            auto_input_helper_path="/tmp/venus_evcharger_auto_input_helper.py",
        )

    @classmethod
    def _owner(
        cls,
        service: object,
        functions: ServiceFunctionBundle | None = None,
    ) -> ServiceControllerOwner:
        owner = ServiceControllerOwner(service, functions or cls._function_bundle())
        setattr(service, "controllers", owner)
        return owner

    @classmethod
    def _controller(
        cls,
        service: object,
        functions: ServiceFunctionBundle | None = None,
    ) -> ServiceBootstrapController:
        controller = cls._owner(service, functions).bootstrap
        if not isinstance(controller, ServiceBootstrapController):
            raise TypeError("controller owner did not compose ServiceBootstrapController")
        return controller


__all__ = [
    "MONTH_WINDOW_DEFAULTS",
    "MagicMock",
    "Path",
    "ServiceBootstrapController",
    "ServiceBootstrapControllerTestCase",
    "ServiceControllerOwner",
    "ServiceFunctionBundle",
    "SimpleNamespace",
    "_FakeGobjectTimers",
    "_RecordingGatewayPublication",
    "_enable_fault_diagnostics",
    "_install_signal_logging",
    "_logging_level_from_config",
    "_request_mainloop_quit",
    "_run_service_loop",
    "_seasonal_month_windows",
    "configparser",
    "datetime",
    "patch",
    "run_service_main",
    "tempfile",
]
