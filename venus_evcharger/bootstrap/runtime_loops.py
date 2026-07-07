# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime worker and timer registration helpers for service bootstrap."""

from __future__ import annotations

import logging
import os
from typing import Any

from venus_evcharger.bootstrap.runtime_metadata import topology_configured

OPTIONAL_RUNTIME_HOOKS = (
    "_start_update_worker",
    "_start_control_command_worker",
    "_start_mainloop_watchdog",
    "_start_companion_dbus_bridge",
)


def call_runtime_hook(svc: Any, name: str) -> bool:
    """Call one optional runtime hook when present."""
    hook = getattr(svc, name, None)
    if not callable(hook):
        return False
    hook()
    return True


def start_runtime_optional_hooks(svc: Any) -> None:
    """Start optional runtime workers and companions."""
    for name in OPTIONAL_RUNTIME_HOOKS:
        call_runtime_hook(svc, name)


def register_runtime_timers(svc: Any, gobject_module: Any) -> None:
    """Register runtime GLib timers."""
    schedule_update_cycle = getattr(svc, "_schedule_update_cycle", None)
    gobject_module.timeout_add(
        svc.poll_interval_ms,
        schedule_update_cycle if callable(schedule_update_cycle) else svc._update,
    )
    flush_dbus_publish_queue = getattr(svc, "_flush_dbus_publish_queue", None)
    if callable(flush_dbus_publish_queue):
        gobject_module.timeout_add(int(getattr(svc, "_dbus_publish_flush_interval_ms", 200)), flush_dbus_publish_queue)
    mainloop_heartbeat_tick = getattr(svc, "_mainloop_heartbeat_tick", None)
    if callable(mainloop_heartbeat_tick):
        gobject_module.timeout_add(1000, mainloop_heartbeat_tick)


def start_runtime_loops(svc: Any, gobject_module: Any) -> None:
    """Register DBus paths, start background workers, and arm timers."""
    call_runtime_hook(svc, "_mark_mainloop_thread")
    if topology_configured(svc):
        svc._start_io_worker()
    else:
        logging.info("No load topology is configured yet; skipping runtime I/O worker startup")
    svc._start_control_api_server()
    start_runtime_optional_hooks(svc)
    logging.info(
        "Initialized Venus EV charger service pid=%s runtime_state=%s %s",
        os.getpid(),
        svc.runtime_state_path,
        svc._state_summary(),
    )
    register_runtime_timers(svc, gobject_module)
    gobject_module.timeout_add(svc.sign_of_life_minutes * 60 * 1000, svc._sign_of_life)
