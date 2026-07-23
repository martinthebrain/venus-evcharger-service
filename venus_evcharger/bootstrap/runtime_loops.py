# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime worker and timer registration helpers for service bootstrap."""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

class _RuntimeFacade(Protocol):
    def start_io_worker(self) -> None: ...
    def start_update_worker(self) -> None: ...
    def start_control_command_worker(self) -> None: ...
    def start_mainloop_watchdog(self) -> None: ...
    def schedule_update_cycle(self) -> bool: ...
    def mainloop_heartbeat_tick(self) -> bool: ...


class _StateFacade(Protocol):
    def start_companion_bridge(self) -> None: ...
    def summary(self) -> str: ...


class _UpdateFacade(Protocol):
    def update(self) -> bool: ...
    def sign_of_life(self) -> bool: ...


class _ControlFacade(Protocol):
    def start_server(self) -> None: ...


@runtime_checkable
class RuntimeLoopService(Protocol):
    runtime: _RuntimeFacade
    state: _StateFacade
    update: _UpdateFacade
    control: _ControlFacade
    poll_interval_ms: int
    sign_of_life_minutes: int
    runtime_state_path: str
    topology_configured: bool


class _GobjectTimers(Protocol):
    def timeout_add(self, interval: int, callback: object) -> object: ...


def register_runtime_timers(svc: RuntimeLoopService, gobject_module: _GobjectTimers) -> None:
    """Register runtime GLib timers."""
    gobject_module.timeout_add(
        svc.poll_interval_ms,
        svc.runtime.schedule_update_cycle,
    )
    gobject_module.timeout_add(1000, svc.runtime.mainloop_heartbeat_tick)


def start_runtime_loops(svc: RuntimeLoopService, gobject_module: _GobjectTimers) -> None:
    """Register DBus paths, start background workers, and arm timers."""
    if svc.topology_configured:
        svc.runtime.start_io_worker()
    else:
        logging.info("No load topology is configured yet; skipping runtime I/O worker startup")
    svc.control.start_server()
    svc.runtime.start_update_worker()
    svc.runtime.start_control_command_worker()
    svc.runtime.start_mainloop_watchdog()
    svc.state.start_companion_bridge()
    logging.info(
        "Initialized Venus EV charger service pid=%s runtime_state=%s %s",
        os.getpid(),
        svc.runtime_state_path,
        svc.state.summary(),
    )
    register_runtime_timers(svc, gobject_module)
    gobject_module.timeout_add(svc.sign_of_life_minutes * 60 * 1000, svc.update.sign_of_life)
