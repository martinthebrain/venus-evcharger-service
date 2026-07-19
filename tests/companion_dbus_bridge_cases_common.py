# SPDX-License-Identifier: GPL-3.0-or-later
import sys
import unittest
from unittest.mock import MagicMock, patch

from tests.support.publish_runtime import PublishServiceHarness as SimpleNamespace

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.companion import EnergyCompanionDbusBridge


class _FakeVeDbusService:
    def __init__(self, name: str, register: bool = False, **_kwargs: object) -> None:
        self.name = name
        self.register_requested = register
        self.paths: dict[str, object] = {}
        self.registered = False

    def add_path(self, path: str, value: object, **_kwargs: object) -> None:
        self.paths[path] = value

    def register(self) -> None:
        self.registered = True

    def __getitem__(self, path: str) -> object:
        return self.paths[path]

    def __setitem__(self, path: str, value: object) -> None:
        self.paths[path] = value


__all__ = [name for name in globals() if not name.startswith("__")]
