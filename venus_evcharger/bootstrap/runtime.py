# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Venus EV charger service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop
"""

from __future__ import annotations

from typing import Any

from venus_evcharger.bootstrap.config import _ServiceBootstrapConfig
from venus_evcharger.bootstrap.runtime_controllers import initialize_runtime_controllers
from venus_evcharger.bootstrap.runtime_loops import start_runtime_loops
from venus_evcharger.bootstrap.runtime_metadata import (
    apply_device_metadata,
    fetch_device_info_with_fallback,
)
from venus_evcharger.bootstrap.runtime_virtual_state import (
    initialize_virtual_state,
)


class _ServiceBootstrapRuntime(_ServiceBootstrapConfig):
    def initialize_controllers(self) -> None:
        """Create the controller objects used by the service runtime."""
        initialize_runtime_controllers(
            self.service,
            age_seconds=self._age_seconds,
            health_code=self._health_code,
            mode_uses_auto_logic=self._mode_uses_auto_logic,
            normalize_mode=self._normalize_mode,
            phase_values=self._phase_values,
        )

    def initialize_virtual_state(self) -> None:
        """Initialize the writable EV charger state exposed on DBus."""
        initialize_virtual_state(self.service, self._normalize_mode)

    def restore_runtime_state(self) -> None:
        """Restore RAM-backed state and initialize worker bookkeeping."""
        svc = self.service
        svc._load_runtime_state()
        svc._startup_manual_target = (
            bool(svc.virtual_enable or svc.virtual_startstop)
            if not self._mode_uses_auto_logic(svc.virtual_mode)
            else None
        )
        svc._init_worker_state()

    def apply_device_metadata(self) -> None:
        """Fetch Shelly metadata and apply UI-facing identity fields."""
        apply_device_metadata(
            self.service,
            read_version=self._read_version,
            fetch_device_info=self.fetch_device_info_with_fallback,
        )

    def start_runtime_loops(self) -> None:
        """Register DBus paths, start background workers, and arm timers."""
        start_runtime_loops(self.service, self._gobject)

    def fetch_device_info_with_fallback(self) -> dict[str, Any]:
        """Try to fetch Shelly device info, but start with generic metadata if that fails."""
        return fetch_device_info_with_fallback(self.service)
