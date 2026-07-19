# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from venus_evcharger.backend.base import is_switch_backend
from venus_evcharger.backend.registry_contracts import BackendConstructor


def _return_none() -> None:
    return None


def _set_enabled(_enabled: bool) -> None:
    return None


def _set_phase_selection(_selection: object) -> None:
    return None


def _callable_switch(**overrides: object) -> SimpleNamespace:
    methods: dict[str, object] = {
        "capabilities": _return_none,
        "read_switch_state": _return_none,
        "set_enabled": _set_enabled,
        "set_phase_selection": _set_phase_selection,
    }
    methods.update(overrides)
    return SimpleNamespace(**methods)


class TestBackendBaseContracts(unittest.TestCase):
    def test_backend_constructor_has_one_canonical_keyword_config_contract(self) -> None:
        def constructor(service: object, *, config_path: str = "") -> tuple[object, str]:
            return service, config_path

        typed_constructor: BackendConstructor[tuple[object, str]] = constructor
        signature = inspect.signature(typed_constructor)
        config_path = signature.parameters["config_path"]

        self.assertEqual(config_path.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(config_path.default, "")
        service = object()
        self.assertEqual(typed_constructor(service), (service, ""))
        self.assertEqual(typed_constructor(service, config_path="config.ini"), (service, "config.ini"))
        with self.assertRaises(TypeError):
            inspect.signature(typed_constructor).bind(service, "config.ini")

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
