# SPDX-License-Identifier: GPL-3.0-or-later
"""Flat composition root for Venus EV charger service bootstrap."""

from __future__ import annotations

import configparser
import faulthandler
import logging
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass

from venus_evcharger.app.bootstrap_support import (
    enable_fault_diagnostics as _enable_fault_diagnostics_impl,
    install_signal_logging as _install_signal_logging_impl,
    logging_level_from_config as _logging_level_from_config,
    request_mainloop_quit as _request_mainloop_quit_impl,
    run_service_loop as _run_service_loop_impl,
)
from venus_evcharger.bootstrap.config import ServiceConfigLoader
from venus_evcharger.bootstrap.config_auto import AutoConfigLoader
from venus_evcharger.bootstrap.config_backend import BackendConfigLoader
from venus_evcharger.bootstrap.config_identity import IdentityConfigLoader
from venus_evcharger.bootstrap.contracts import (
    BootstrapDependencies,
    MonthWindow,
    require_gobject_timers,
)
from venus_evcharger.bootstrap.publication import EvcsPublicationRegistrar
from venus_evcharger.bootstrap.runtime import RuntimeInitializer


@dataclass(frozen=True)
class ServiceBootstrapComponents:
    """Concrete components owned by one bootstrap composition root."""

    identity: IdentityConfigLoader
    backend: BackendConfigLoader
    auto: AutoConfigLoader
    config: ServiceConfigLoader
    runtime: RuntimeInitializer
    publication: EvcsPublicationRegistrar


def _enable_fault_diagnostics() -> None:
    """Enable crash diagnostics when available."""
    _enable_fault_diagnostics_impl(faulthandler, logging)


def _install_signal_logging(quit_callback: Callable[[], None] | None = None) -> None:
    """Install signal handlers that log and request a clean GLib-loop shutdown."""
    _install_signal_logging_impl(signal, logging, os, quit_callback)


def _request_mainloop_quit(gobject_module: object, mainloop: object) -> None:
    """Request a clean GLib shutdown, preferring idle_add when available."""
    _request_mainloop_quit_impl(gobject_module, mainloop, logging)


def _run_service_loop(service_class: Callable[[], object], gobject_module: object) -> None:
    """Instantiate the service and enter the GLib main loop."""
    _run_service_loop_impl(
        service_class,
        gobject_module,
        _install_signal_logging,
        _request_mainloop_quit,
        logging,
    )


class ServiceBootstrapController:
    """Public bootstrap facade and owner of explicit bootstrap components."""

    def __init__(
        self,
        service: object,
        *,
        normalize_phase_func: Callable[[object], str],
        normalize_mode_func: Callable[[object], int],
        mode_uses_auto_logic_func: Callable[[object], bool],
        month_window_func: MonthWindow,
        read_version_func: Callable[[str], str],
        gobject_module: object,
        script_path: str,
    ) -> None:
        self.service = service
        self.dependencies = BootstrapDependencies(
            normalize_phase=normalize_phase_func,
            normalize_mode=normalize_mode_func,
            mode_uses_auto_logic=mode_uses_auto_logic_func,
            month_window=month_window_func,
            read_version=read_version_func,
            gobject=gobject_module,
            script_path=script_path,
        )
        identity = IdentityConfigLoader(service, normalize_phase_func)
        backend = BackendConfigLoader(service)
        auto = AutoConfigLoader(service, month_window_func)
        self.components = ServiceBootstrapComponents(
            identity=identity,
            backend=backend,
            auto=auto,
            config=ServiceConfigLoader(service, identity, backend, auto),
            runtime=RuntimeInitializer(
                service,
                normalize_mode=normalize_mode_func,
                mode_uses_auto_logic=mode_uses_auto_logic_func,
                read_version=read_version_func,
                gobject=require_gobject_timers(gobject_module),
            ),
            publication=EvcsPublicationRegistrar(service, script_path=script_path),
        )

    def initialize_service(self) -> None:
        """Fully initialize the wallbox service instance."""
        bootstrap_steps = (
            ("load-runtime-configuration", self.load_runtime_configuration),
            ("prepare-runtime-state", self.prepare_runtime_state),
            ("initialize-virtual-state", self.initialize_virtual_state),
            ("initialize-controllers", self.initialize_controllers),
            ("restore-runtime-state", self.restore_runtime_state),
            ("apply-device-metadata", self.apply_device_metadata),
            ("register-evcs-publication", self.register_evcs_publication),
            ("start-runtime-loops", self.start_runtime_loops),
        )
        for step_name, step_func in bootstrap_steps:
            logging.info("Bootstrap step start: %s", step_name)
            step_func()
            logging.info("Bootstrap step complete: %s", step_name)

    def load_runtime_configuration(self) -> None:
        """Load and validate all runtime configuration through its components."""
        self.components.config.load()

    def initialize_controllers(self) -> None:
        """Create the configured runtime controller graph."""
        self.components.runtime.initialize_controllers()

    def prepare_runtime_state(self) -> None:
        """Initialize shared runtime state before phase-dependent controllers."""
        self.components.runtime.prepare_runtime_state()

    def initialize_virtual_state(self) -> None:
        """Initialize DBus-facing volatile state."""
        self.components.runtime.initialize_virtual_state()

    def restore_runtime_state(self) -> None:
        """Restore RAM-backed runtime state and worker bookkeeping."""
        self.components.runtime.restore_runtime_state()

    def apply_device_metadata(self) -> None:
        """Apply device metadata used by the GUI and management paths."""
        self.components.runtime.apply_device_metadata()

    def register_evcs_publication(self) -> None:
        """Enqueue the complete semantic EVCS registration."""
        self.components.publication.register()

    def start_runtime_loops(self) -> None:
        """Start workers and register GLib timers."""
        self.components.runtime.start_runtime_loops()

    def fetch_device_info_with_fallback(self) -> dict[str, object]:
        """Return startup device metadata with bounded network fallback."""
        return self.components.runtime.fetch_device_info_with_fallback()


def run_service_main(service_class: Callable[[], object], config_path: str, gobject_module: object) -> None:
    """Run the configured wallbox service process."""
    config = configparser.ConfigParser()
    config.read(config_path)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=_logging_level_from_config(config),
    )

    try:
        logging.info("Start Venus EV charger service pid=%s", os.getpid())
        _enable_fault_diagnostics()
        _run_service_loop(service_class, gobject_module)
    except Exception as error:  # pylint: disable=broad-except
        logging.critical("Error at main pid=%s", os.getpid(), exc_info=error)
        raise
