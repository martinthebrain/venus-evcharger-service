# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections import deque
from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from venus_evcharger.auto.policy import AutoPolicy
from venus_evcharger.auto.policy_settings import AUTO_POLICY_SETTING_BY_PATH
from venus_evcharger.ports import WriteControllerPort
from venus_evcharger.controllers.write_snapshot import _snapshot_dbus_paths
from venus_evcharger.controllers.write import DbusWriteController
from venus_evcharger.dbus_gateway import EVCS_FIELD_TO_PATH

__all__ = [
    "Any",
    "AUTO_POLICY_SETTING_BY_PATH",
    "AutoPolicy",
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
    "write_controller",
    "write_port",
]


def _fixture_callback(service: object, name: str, default: Callable[..., object]) -> Callable[..., object]:
    callback = getattr(service, name, default)
    if not callable(callback):
        raise TypeError(f"write-controller fixture attribute {name} is not callable")
    return cast(Callable[..., object], callback)


class _WriteAutoRole:
    """Canonical Auto role backed by one write-controller scenario fixture."""

    def __init__(self, service: object) -> None:
        self._service = service

    def clear_samples(self) -> object:
        def default() -> None:
            samples = getattr(self._service, "auto_samples", None)
            if hasattr(samples, "clear"):
                samples.clear()

        return _fixture_callback(self._service, "_clear_auto_samples", default)()

    def normalize_mode(self, value: object) -> object:
        def default(candidate: object) -> int:
            if isinstance(candidate, bool):
                return int(candidate)
            if isinstance(candidate, (int, float, str)):
                return int(candidate)
            return 0

        return _fixture_callback(self._service, "_normalize_mode", default)(value)

    def mode_uses_auto_logic(self, mode: object) -> object:
        def default(candidate: object) -> bool:
            return int(self.normalize_mode(candidate)) in (1, 2)

        return _fixture_callback(self._service, "_mode_uses_auto_logic", default)(mode)


class _WriteRuntimeRole:
    """Canonical runtime effects used by write-controller scenarios."""

    def __init__(self, service: object) -> None:
        self._service = service

    def queue_relay_command(self, relay_on: bool, current_time: float) -> object:
        return _fixture_callback(self._service, "_queue_relay_command", lambda *_args: None)(
            relay_on,
            current_time,
        )

    def publish_local_pm_status(self, relay_on: bool, current_time: float) -> object:
        return _fixture_callback(
            self._service,
            "_publish_local_pm_status",
            lambda output, _now: {"output": output},
        )(relay_on, current_time)

    def worker_snapshot(self) -> object:
        return _fixture_callback(
            self._service,
            "_get_worker_snapshot",
            lambda: getattr(self._service, "_worker_snapshot", {}),
        )()

    def pending_relay_command(self) -> object:
        return _fixture_callback(
            self._service,
            "_peek_pending_relay_command",
            lambda: (
                getattr(self._service, "_pending_relay_state", None),
                getattr(self._service, "_pending_relay_requested_at", None),
            ),
        )()

    def update_worker_snapshot(self, **fields: object) -> object:
        def default(**updates: object) -> None:
            snapshot = getattr(self._service, "_worker_snapshot", None)
            if isinstance(snapshot, dict):
                snapshot.update(updates)

        return _fixture_callback(self._service, "_update_worker_snapshot", default)(**fields)

    def phase_selection_requires_pause(self) -> object:
        return _fixture_callback(self._service, "_phase_selection_requires_pause", lambda: False)()

    def apply_phase_selection(self, selection: object) -> object:
        return _fixture_callback(self._service, "_apply_phase_selection", lambda value: value)(selection)


class _WriteStateRole:
    """Canonical publication and persistence role for write scenarios."""

    def __init__(self, service: object) -> None:
        self._service = service

    def publish_field(self, field: str, value: object, now: float, *, force: bool = False) -> object:
        return _fixture_callback(self._service, "_publish_dbus_field", lambda *_args, **_kwargs: False)(
            field,
            value,
            now,
            force=force,
        )

    def summary(self) -> object:
        return _fixture_callback(self._service, "_state_summary", lambda: "state")()

    def save_runtime_state(self) -> object:
        return _fixture_callback(self._service, "_save_runtime_state", lambda: None)()

    def save_runtime_overrides(self) -> object:
        return _fixture_callback(self._service, "_save_runtime_overrides", lambda: None)()

    def validate_runtime_config(self) -> object:
        return _fixture_callback(self._service, "_validate_runtime_config", lambda: None)()


def write_port(service: Any) -> WriteControllerPort:
    """Compose the real write port with explicit roles for one test fixture."""
    if not hasattr(service, "auto"):
        service.auto = _WriteAutoRole(service)
    if not hasattr(service, "runtime"):
        service.runtime = _WriteRuntimeRole(service)
    if not hasattr(service, "state"):
        service.state = _WriteStateRole(service)
    return WriteControllerPort(service)


def write_controller(service: Any) -> DbusWriteController:
    """Build the production controller over the canonical test composition."""
    return DbusWriteController(write_port(service))



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
