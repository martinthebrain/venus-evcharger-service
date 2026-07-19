# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from tests.support.publish_runtime import PublishServiceHarness as SimpleNamespace
from venus_evcharger.publish.dbus import DbusPublishController


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
    "DbusPublishController",
    "DbusPublishControllerTestCase",
    "MagicMock",
    "SimpleNamespace",
    "patch",
]
