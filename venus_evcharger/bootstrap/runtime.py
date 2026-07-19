# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-initialization component for service bootstrap."""

from __future__ import annotations

from collections.abc import Callable

from venus_evcharger.bootstrap.contracts import (
    GobjectTimersPort,
    require_controller_owner,
    require_runtime_state,
    require_worker_runtime,
)
from venus_evcharger.bootstrap.runtime_loops import RuntimeLoopService, start_runtime_loops
from venus_evcharger.bootstrap.runtime_metadata import (
    apply_device_metadata,
    fetch_device_info_with_fallback,
)
from venus_evcharger.bootstrap.runtime_virtual_state import initialize_virtual_state


class RuntimeInitializer:
    """Initialize controllers, volatile state, metadata, workers, and timers."""

    def __init__(
        self,
        service: object,
        *,
        normalize_mode: Callable[[object], int],
        mode_uses_auto_logic: Callable[[object], bool],
        read_version: Callable[[str], str],
        gobject: GobjectTimersPort,
    ) -> None:
        self._service = service
        self._normalize_mode = normalize_mode
        self._mode_uses_auto_logic = mode_uses_auto_logic
        self._read_version = read_version
        self._gobject = gobject

    def initialize_controllers(self) -> None:
        """Create the controller objects used by the service runtime."""
        require_controller_owner(self._service).initialize_runtime()

    def initialize_virtual_state(self) -> None:
        """Initialize the writable EV charger state exposed on DBus."""
        initialize_virtual_state(self._service, self._normalize_mode)

    def restore_runtime_state(self) -> None:
        """Restore RAM-backed state and initialize worker bookkeeping."""
        svc = self._service
        require_runtime_state(svc).load_runtime_state()
        virtual_mode = getattr(svc, "virtual_mode", 0)
        manual_target = None
        if not self._mode_uses_auto_logic(virtual_mode):
            manual_target = bool(getattr(svc, "virtual_enable", 0) or getattr(svc, "virtual_startstop", 0))
        setattr(svc, "_startup_manual_target", manual_target)
        require_worker_runtime(svc).initialize_worker_state()

    def apply_device_metadata(self) -> None:
        """Fetch device metadata and apply UI-facing identity fields."""
        apply_device_metadata(
            self._service,
            read_version=self._read_version,
            fetch_device_info=self.fetch_device_info_with_fallback,
        )

    def start_runtime_loops(self) -> None:
        """Start background workers and arm GLib timers."""
        if not isinstance(self._service, RuntimeLoopService):
            raise TypeError("bootstrap service does not implement RuntimeLoopService")
        start_runtime_loops(self._service, self._gobject)

    def fetch_device_info_with_fallback(self) -> dict[str, object]:
        """Fetch device info or return generic metadata after bounded retries."""
        return dict(fetch_device_info_with_fallback(self._service))
