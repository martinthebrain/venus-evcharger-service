# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure bootstrap helper implementations with injectable runtime dependencies."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from types import FrameType
from typing import Any


def logging_level_from_config(config: configparser.ConfigParser, default: str = "INFO") -> str:
    """Read the configured log level from the DEFAULT section."""
    if "DEFAULT" not in config:
        return default
    return config["DEFAULT"].get("Logging", default).upper()


def enable_fault_diagnostics(faulthandler_module: Any, logging_module: Any) -> None:
    """Enable crash diagnostics when available."""
    try:
        faulthandler_module.enable(all_threads=True)
    except (OSError, RuntimeError) as error:
        logging_module.debug("faulthandler.enable() unavailable: %s", error)


def install_signal_logging(
    signal_module: Any,
    logging_module: Any,
    os_module: Any,
    quit_callback: Callable[[], None] | None = None,
) -> None:
    """Install signal handlers that log and request a clean GLib-loop shutdown."""

    def _log_signal(signum: int, _frame: FrameType | None) -> None:
        logging_module.warning("Received signal %s in pid=%s", signum, os_module.getpid())
        if quit_callback is None:
            return
        try:
            quit_callback()
        except Exception as error:  # pylint: disable=broad-except
            logging_module.debug("Unable to request shutdown after signal %s: %s", signum, error)

    for signum in (
        getattr(signal_module, "SIGTERM", None),
        getattr(signal_module, "SIGINT", None),
        getattr(signal_module, "SIGHUP", None),
    ):
        if signum is None:
            continue
        try:
            signal_module.signal(signum, _log_signal)
        except (OSError, RuntimeError, ValueError) as error:
            logging_module.debug("Unable to install signal handler for %s: %s", signum, error)


def request_mainloop_quit(gobject_module: Any, mainloop: Any, logging_module: Any) -> None:
    """Request a clean GLib shutdown, preferring idle_add when available."""
    idle_add = getattr(gobject_module, "idle_add", None)
    if callable(idle_add):
        try:
            idle_add(mainloop.quit)
            return
        except (RuntimeError, TypeError, ValueError) as error:
            logging_module.debug("Unable to schedule GLib shutdown via idle_add: %s", error)
    mainloop.quit()


def run_service_loop(
    service_class: Callable[[], Any],
    gobject_module: Any,
    install_signal_logging_func: Callable[[Callable[[], None] | None], None],
    request_mainloop_quit_func: Callable[[Any, Any], None],
    logging_module: Any,
) -> None:
    """Instantiate the service and enter the GLib main loop."""
    logging_module.info("Instantiating Venus EV charger service bootstrap")
    service_class()
    logging_module.info("Service bootstrap completed; preparing GLib main loop")
    mainloop = gobject_module.MainLoop()

    def request_shutdown() -> None:
        request_mainloop_quit_func(gobject_module, mainloop)

    install_signal_logging_func(request_shutdown)
    logging_module.info("Connected to dbus, and switching over to gobject.MainLoop() (= event based)")
    mainloop.run()
