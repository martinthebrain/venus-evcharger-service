# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.bootstrap.controller import (
    ServiceBootstrapController,
    _enable_fault_diagnostics,
    _install_signal_logging,
    _logging_level_from_config,
    _request_mainloop_quit,
    _run_service_loop,
    run_service_main,
)
from venus_evcharger.bootstrap.config_shared import MONTH_WINDOW_DEFAULTS, _seasonal_month_windows
from venus_evcharger.dbus_gateway import venus_path_writeable


class _FakeDbusService:
    def __init__(self) -> None:
        self.paths: dict[str, dict[str, object]] = {}
        self.register_called = False

    def add_path(self, path: str, value: object, **kwargs: object) -> None:
        kwargs.setdefault("writeable", venus_path_writeable(path))
        self.paths[path] = {"value": value, **kwargs}

    def register(self) -> None:
        self.register_called = True


class _FakeGobjectTimers:
    def __init__(self) -> None:
        self.timeout_calls: list[tuple[int, object]] = []

    def timeout_add(self, interval: int, callback: object) -> object:
        call = (interval, callback)
        self.timeout_calls.append(call)
        return call


class ServiceBootstrapControllerTestCase(unittest.TestCase):
    @staticmethod
    def _controller(service: object) -> ServiceBootstrapController:
        return ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            read_version_func=lambda _name: "1.0",
            gobject_module=_FakeGobjectTimers(),
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
    "MONTH_WINDOW_DEFAULTS",
    "MagicMock",
    "Path",
    "ServiceBootstrapController",
    "ServiceBootstrapControllerTestCase",
    "SimpleNamespace",
    "_FakeDbusService",
    "_FakeGobjectTimers",
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
