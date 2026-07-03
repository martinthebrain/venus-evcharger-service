# SPDX-License-Identifier: GPL-3.0-or-later
"""Async runtime helpers that keep the GLib/DBus main loop responsive."""

from __future__ import annotations

from venus_evcharger.runtime.async_mainloop_publish import _RuntimeAsyncMainloopPublish


class _RuntimeAsyncMainloop(_RuntimeAsyncMainloopPublish):
    """Compose async runtime queue, executor, and watchdog helpers."""
