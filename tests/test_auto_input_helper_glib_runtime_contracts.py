# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import unittest
from collections.abc import Callable

from venus_evcharger.inputs.helper.glib_runtime import GlibRuntime
from tests.support.auto_input_helper import FakeLoop


class FakeGlibModule:
    def __init__(self, main_loop: object) -> None:
        self.main_loop = main_loop
        self.idle_callbacks: list[Callable[[], object]] = []
        self.timeouts: list[tuple[int, Callable[[], object]]] = []

    def MainLoop(self) -> object:  # noqa: N802 - mirrors the GLib API
        return self.main_loop

    def idle_add(self, callback: Callable[[], object]) -> int:
        self.idle_callbacks.append(callback)
        return 11

    def timeout_add(self, interval_ms: int, callback: Callable[[], object]) -> int:
        self.timeouts.append((interval_ms, callback))
        return 12


class AutoInputHelperGlibRuntimeContracts(unittest.TestCase):
    def test_runtime_requires_the_narrow_glib_surface(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "required helper API"):
            GlibRuntime(object())

    def test_main_loop_must_satisfy_its_contract(self) -> None:
        runtime = GlibRuntime(FakeGlibModule(object()))
        with self.assertRaisesRegex(RuntimeError, "MainLoop"):
            runtime.create_main_loop()

    def test_runtime_delegates_only_required_operations(self) -> None:
        loop = FakeLoop()
        module = FakeGlibModule(loop)
        runtime = GlibRuntime(module)
        callback = lambda: False
        self.assertIs(runtime.create_main_loop(), loop)
        self.assertEqual(runtime.idle_add(callback), 11)
        self.assertEqual(runtime.timeout_add(250, callback), 12)
        self.assertEqual(module.idle_callbacks, [callback])
        self.assertEqual(module.timeouts, [(250, callback)])


if __name__ == "__main__":
    unittest.main()
