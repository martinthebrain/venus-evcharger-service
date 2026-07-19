# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.controllers.auto import AutoDecisionController
from venus_evcharger.auto.logic_types import NO_RELAY_DECISION
from venus_evcharger.ports.auto import AutoDecisionPort
from tests.venus_evcharger_test_fixtures import make_auto_controller_service


def _health_code(reason: str) -> int:
    return {
        "grid-missing": 1,
        "inputs-missing": 2,
        "auto-start": 3,
        "battery-soc-missing": 4,
        "battery-soc-missing-allowed": 5,
        "waiting-grid": 6,
        "waiting": 7,
        "autostart-disabled": 8,
        "averaging": 9,
        "mode-transition": 10,
        "waiting-grid-recovery": 11,
        "scheduled-night-charge": 12,
    }.get(reason, 99)


def _mode_uses_auto_logic(mode) -> bool:
    return int(mode) in (1, 2)


def utc_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


class AutoDecisionControllerTestCase(unittest.TestCase):
    def _make_controller(self):
        service = make_auto_controller_service()
        controller = AutoDecisionController(AutoDecisionPort(service), _health_code, _mode_uses_auto_logic)
        return controller, service


__all__ = [
    "AutoDecisionController",
    "AutoDecisionControllerTestCase",
    "MagicMock",
    "NO_RELAY_DECISION",
    "SimpleNamespace",
    "_health_code",
    "_mode_uses_auto_logic",
    "datetime",
    "deque",
    "make_auto_controller_service",
    "patch",
    "utc_timestamp",
]
