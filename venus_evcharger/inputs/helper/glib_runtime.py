# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed boundary around the dynamically provided GLib runtime."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, runtime_checkable

from venus_evcharger.inputs.helper.contracts import MainLoopPort


@runtime_checkable
class _GlibModulePort(Protocol):  # pragma: no cover
    def MainLoop(self) -> object: ...  # noqa: N802 - GLib owns this API name

    def idle_add(self, callback: Callable[[], object]) -> int: ...

    def timeout_add(self, interval_ms: int, callback: Callable[[], object]) -> int: ...


class GlibRuntime:
    """Expose only the GLib operations required by the helper process."""

    def __init__(self, module: object) -> None:
        if not isinstance(module, _GlibModulePort):
            raise RuntimeError("GLib runtime does not provide the required helper API")
        self._module = module

    def create_main_loop(self) -> MainLoopPort:
        main_loop = self._module.MainLoop()
        if not isinstance(main_loop, MainLoopPort):
            raise RuntimeError("GLib MainLoop does not satisfy the helper contract")
        return main_loop

    def idle_add(self, callback: Callable[[], object]) -> int:
        return int(self._module.idle_add(callback))

    def timeout_add(self, interval_ms: int, callback: Callable[[], object]) -> int:
        return int(self._module.timeout_add(interval_ms, callback))


GLIB_RUNTIME = GlibRuntime(import_module("gi.repository.GLib"))
