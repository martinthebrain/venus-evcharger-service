# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections import deque
from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from venus_evcharger.ports import WriteControllerPort
from venus_evcharger.controllers.write_snapshot import _snapshot_dbus_paths
from venus_evcharger.controllers.write import DbusWriteController
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH

__all__ = [
    "Any",
    "Callable",
    "DbusWriteController",
    "DbusWriteControllerTestBase",
    "MagicMock",
    "SimpleNamespace",
    "WriteControllerPort",
    "_snapshot_dbus_paths",
    "deque",
    "partial",
    "patch",
]



class DbusWriteControllerTestBase(unittest.TestCase):
    @staticmethod
    def _normalize_mode(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float, str)):
            return int(value)
        return 0

    @staticmethod
    def _normalize_mode_5_to_2(value: object) -> int:
        mode = DbusWriteControllerTestBase._normalize_mode(value)
        return 2 if mode == 5 else mode

    @staticmethod
    def _mode_uses_auto_logic(mode: object) -> bool:
        return DbusWriteControllerTestBase._normalize_mode(mode) in (1, 2)

    @staticmethod
    def _clear_auto_samples(service: Any) -> None:
        service.auto_samples.clear()

    @staticmethod
    def _state_summary() -> str:
        return "state"

    @staticmethod
    def _publish_side_effect(service: Any) -> Callable[..., bool]:
        def _publish(
            path: str,
            value: object,
            _now: float | None = None,
            force: bool = False,
            **_kwargs: object,
        ) -> bool:
            service._dbusservice[path] = value
            return force

        return _publish

    @staticmethod
    def _publish_field_side_effect(service: Any) -> Callable[..., bool]:
        def _publish(
            field: str,
            value: object,
            _now: float | None = None,
            force: bool = False,
            **_kwargs: object,
        ) -> bool:
            service._dbusservice[EVCS_FIELD_TO_PATH[str(field)]] = value
            return force

        return _publish

    @staticmethod
    def _apply_phase_selection(service: Any, selection: str) -> str:
        service.requested_phase_selection = selection
        service.active_phase_selection = selection
        return selection
