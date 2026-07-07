# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from venus_evcharger.backend.base import BackendConstructor, is_switch_backend


def _callable_switch(**overrides: object) -> SimpleNamespace:
    methods: dict[str, object] = {
        "capabilities": lambda: None,
        "read_switch_state": lambda: None,
        "set_enabled": lambda _enabled: None,
        "set_phase_selection": lambda _selection: None,
    }
    methods.update(overrides)
    return SimpleNamespace(**methods)


class TestBackendBaseContracts(unittest.TestCase):
    def test_backend_constructor_requires_explicit_config_path_argument(self) -> None:
        signature = inspect.signature(BackendConstructor.__call__)

        self.assertIs(signature.parameters["config_path"].default, inspect.Parameter.empty)
        with self.assertRaises(TypeError):
            BackendConstructor.__call__(object(), object())
        self.assertIsNone(BackendConstructor.__call__(object(), object(), "config.ini"))

    def test_is_switch_backend_accepts_complete_callable_surface(self) -> None:
        self.assertTrue(is_switch_backend(_callable_switch()))

    def test_is_switch_backend_rejects_each_missing_surface_method(self) -> None:
        for method_name in ("capabilities", "read_switch_state", "set_enabled", "set_phase_selection"):
            with self.subTest(method_name=method_name):
                service = _callable_switch()
                delattr(service, method_name)

                self.assertFalse(is_switch_backend(service))

    def test_is_switch_backend_rejects_each_non_callable_surface_member(self) -> None:
        for method_name in ("capabilities", "read_switch_state", "set_enabled", "set_phase_selection"):
            with self.subTest(method_name=method_name):
                self.assertFalse(is_switch_backend(_callable_switch(**{method_name: "not-callable"})))


if __name__ == "__main__":
    unittest.main()
