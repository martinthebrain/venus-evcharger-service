#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the isolated, gateway-only auto-input helper process."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from venus_evcharger.inputs.helper.config_runtime import load_auto_input_helper_settings
from venus_evcharger.inputs.helper.contracts import MainLoopPort
from venus_evcharger.inputs.helper.energy_gateway import EnergyRefreshCoordinator, GatewayEnergySnapshots
from venus_evcharger.inputs.helper.glib_runtime import GLIB_RUNTIME
from venus_evcharger.inputs.helper.liveness import HelperLiveness
from venus_evcharger.inputs.helper.snapshot import AtomicSnapshotWriter, SnapshotStore
from venus_evcharger.inputs.helper.sources import AutoInputSources


class AutoInputHelper:
    """Compose helper roles and own only process lifecycle orchestration."""

    def __init__(
        self,
        config_path: str,
        snapshot_path: str | None = None,
        parent_pid: object = None,
        helper_generation: object = None,
        runtime_instance_id: object = None,
    ) -> None:
        self.settings = load_auto_input_helper_settings(
            config_path,
            snapshot_path,
            parent_pid,
            helper_generation,
            runtime_instance_id,
        )
        self.gateway = GatewayEnergySnapshots(self.settings)
        self.sources = AutoInputSources(self.settings, self.gateway)
        self.liveness = HelperLiveness(self.settings)
        self.snapshots = SnapshotStore(
            self.settings,
            self.sources,
            AtomicSnapshotWriter(self.settings),
            self.liveness.stop_requested,
        )
        self.refresh_coordinator = EnergyRefreshCoordinator(
            self.gateway,
            self.snapshots,
            self.liveness.stop_requested,
        )
        self._main_loop: MainLoopPort | None = None

    def run(self) -> None:
        """Run GLib orchestration while liveness remains independent in threads."""
        self._install_signal_handlers()
        logging.info(
            "Start auto input helper pid=%s parent=%s snapshot=%s",
            os.getpid(),
            self.settings.parent_pid,
            self.settings.snapshot_path,
        )
        main_loop = GLIB_RUNTIME.create_main_loop()
        self._main_loop = main_loop
        self.liveness.bind(self.snapshots, main_loop)
        self.snapshots.write_lifecycle("starting")
        self.liveness.start()
        self._install_timers()
        self._schedule_initial_refresh()
        try:
            main_loop.run()
        finally:
            self.liveness.stop()
            self.refresh_coordinator.reset()
            self.sources.close()
            logging.info("Auto input helper stopping pid=%s", os.getpid())

    def _handle_signal(self, signum: int, _frame: object) -> None:
        logging.info("Auto input helper received signal %s", signum)
        self.liveness.request_stop()

    def _install_signal_handlers(self) -> None:
        for signum in _signal_values():
            try:
                signal.signal(signum, self._handle_signal)
            except (OSError, RuntimeError, ValueError) as error:
                logging.debug("Unable to install auto-input-helper signal handler signal=%s: %s", signum, error)

    def _install_timers(self) -> None:
        GLIB_RUNTIME.timeout_add(
            max(200, int(self.settings.poll_interval_seconds * 1000)),
            self.snapshots.poll,
        )
        GLIB_RUNTIME.timeout_add(
            max(5000, int(self.settings.validation_poll_seconds * 1000)),
            self.snapshots.validation_poll,
        )
        GLIB_RUNTIME.timeout_add(
            max(5000, int(self.settings.topology_refresh_seconds * 1000)),
            self.refresh_coordinator.timer_tick,
        )
        GLIB_RUNTIME.timeout_add(1000, self.liveness.parent_watchdog_tick)

    def _schedule_initial_refresh(self) -> None:
        def refresh() -> bool:
            if self.liveness.stop_requested():
                return False
            self.snapshots.write_lifecycle("initializing")
            return self.refresh_coordinator.refresh()

        GLIB_RUNTIME.idle_add(refresh)


def main(argv: list[str] | None = None) -> int:
    args = _main_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=logging.INFO,
    )
    helper = AutoInputHelper(
        args.config_path,
        args.snapshot_path,
        args.parent_pid,
        args.helper_generation,
        args.runtime_instance_id,
    )
    helper.run()
    return 0


def _default_config_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "deploy",
        "venus",
        "config.venus_evcharger.ini",
    )


def _main_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Venus EV charger Auto input helper.")
    parser.add_argument("config_path", nargs="?", default=_default_config_path())
    parser.add_argument("snapshot_path", nargs="?")
    parser.add_argument("parent_pid", nargs="?")
    parser.add_argument("helper_generation", nargs="?")
    parser.add_argument("runtime_instance_id", nargs="?")
    return parser.parse_args(argv)


def _signal_values() -> tuple[int, ...]:
    return tuple(
        signum
        for signum in (
            getattr(signal, "SIGTERM", None),
            getattr(signal, "SIGINT", None),
            getattr(signal, "SIGHUP", None),
        )
        if signum is not None
    )


if __name__ == "__main__":
    raise SystemExit(main())
