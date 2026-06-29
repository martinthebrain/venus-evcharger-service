# SPDX-License-Identifier: GPL-3.0-or-later
"""Async runtime helpers that keep the GLib/DBus main loop responsive."""

from __future__ import annotations

from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from venus_evcharger.runtime.async_mainloop_control import _RuntimeAsyncMainloopControl
from venus_evcharger.runtime.async_mainloop_executor import _RuntimeAsyncMainloopExecutor
from venus_evcharger.runtime.async_mainloop_publish import _RuntimeAsyncMainloopPublish
from venus_evcharger.runtime.async_mainloop_state import _RuntimeAsyncMainloopState
from venus_evcharger.runtime.async_mainloop_watchdog import _RuntimeAsyncMainloopWatchdog


class _RuntimeAsyncMainloop(
    _RuntimeAsyncMainloopWatchdog,
    _RuntimeAsyncMainloopControl,
    _RuntimeAsyncMainloopExecutor,
    _RuntimeAsyncMainloopPublish,
    _RuntimeAsyncMainloopState,
    _ComposableControllerMixin,
):
    """Compose async runtime queue, executor, and watchdog helpers."""
