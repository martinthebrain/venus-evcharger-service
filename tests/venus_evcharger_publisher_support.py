# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from tests.gateway_diagnostics_fixtures import gateway_diagnostics_reader
from venus_evcharger.bootstrap.publication import EvcsPublicationOwner
from tests.support.publish_runtime import PublishServiceHarness as SimpleNamespace
from venus_evcharger.ports.gateway_diagnostics import GatewayDiagnosticsReader
from venus_evcharger.publish.dbus import DbusPublishController as _DbusPublishController


def build_publish_controller(
    service: SimpleNamespace,
    age_seconds: Any,
    gateway_diagnostics: GatewayDiagnosticsReader | None = None,
) -> _DbusPublishController:
    """Build a publisher against the real semantic gateway diagnostics port."""
    return _DbusPublishController(
        service,
        age_seconds,
        gateway_diagnostics or gateway_diagnostics_reader(),
        EvcsPublicationOwner(service, script_path="test-service.py"),
    )


class DbusPublishControllerTestCase(unittest.TestCase):
    @staticmethod
    def _age_seconds(_timestamp: Any, _now: float) -> float:
        return 0.0

    @staticmethod
    def _real_age_seconds(timestamp: Any, now: float) -> float:
        if timestamp is None:
            return -1.0
        return float(now) - float(timestamp)

    @staticmethod
    def _never_stale(_now: float) -> bool:
        return False


__all__ = [
    "Any",
    "DbusPublishControllerTestCase",
    "MagicMock",
    "SimpleNamespace",
    "build_publish_controller",
    "patch",
]
