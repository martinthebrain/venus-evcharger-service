# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.bootstrap.controller import (
    MONTH_WINDOW_DEFAULTS,
    ServiceBootstrapController,
    _enable_fault_diagnostics,
    _install_signal_logging,
    _logging_level_from_config,
    _request_mainloop_quit,
    _run_service_loop,
    _seasonal_month_windows,
    run_service_main,
)
from venus_evcharger.dbus_gateway import venus_path_writeable
from venus_evcharger.ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort


class _FakeDbusService:
    def __init__(self) -> None:
        self.paths: dict[str, dict[str, object]] = {}
        self.register_called = False

    def add_path(self, path: str, value: object, **kwargs: object) -> None:
        kwargs.setdefault("writeable", venus_path_writeable(path))
        self.paths[path] = {"value": value, **kwargs}

    def register(self) -> None:
        self.register_called = True


class ServiceBootstrapControllerTestCase(unittest.TestCase):
    @staticmethod
    def _controller(service: object) -> ServiceBootstrapController:
        return ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=MagicMock(),
            script_path="/tmp/venus_evcharger_service.py",
            formatters={
                "kwh": None,
                "a": None,
                "w": None,
                "v": None,
                "status": None,
            },
        )


__all__ = [
    "AutoDecisionPort",
    "MONTH_WINDOW_DEFAULTS",
    "MagicMock",
    "ModuleType",
    "Path",
    "ServiceBootstrapController",
    "ServiceBootstrapControllerTestCase",
    "SimpleNamespace",
    "UpdateCyclePort",
    "WriteControllerPort",
    "_FakeDbusService",
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
    "sys",
    "tempfile",
]
