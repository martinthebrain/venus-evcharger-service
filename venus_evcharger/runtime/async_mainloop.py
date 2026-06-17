# SPDX-License-Identifier: GPL-3.0-or-later
"""Async runtime helpers that keep the GLib/DBus main loop responsive."""

from __future__ import annotations

from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from venus_evcharger.runtime.async_mainloop_control import _RuntimeSupportAsyncMainloopControlMixin
from venus_evcharger.runtime.async_mainloop_executor import _RuntimeSupportAsyncMainloopExecutorMixin
from venus_evcharger.runtime.async_mainloop_publish import _RuntimeSupportAsyncMainloopPublishMixin
from venus_evcharger.runtime.async_mainloop_state import _RuntimeSupportAsyncMainloopStateMixin
from venus_evcharger.runtime.async_mainloop_watchdog import _RuntimeSupportAsyncMainloopWatchdogMixin


class _RuntimeSupportAsyncMainloopMixin(
    _RuntimeSupportAsyncMainloopWatchdogMixin,
    _RuntimeSupportAsyncMainloopControlMixin,
    _RuntimeSupportAsyncMainloopExecutorMixin,
    _RuntimeSupportAsyncMainloopPublishMixin,
    _RuntimeSupportAsyncMainloopStateMixin,
    _ComposableControllerMixin,
):
    """Compose async runtime queue, executor, and watchdog helpers."""
