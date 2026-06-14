#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Venus EV charger service in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import configparser
import argparse
import logging
import os
import signal
import sys
import threading
import time
import uuid
from typing import Any

from gi.repository import GLib
from venus_evcharger.core.shared import (
    AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
    compact_json,
    parse_config_bool as _as_bool,
    write_text_atomically,
)
from venus_evcharger.energy import load_energy_source_settings
from venus_evcharger.inputs.helper import (
    _AutoInputHelperSnapshotMixin,
    _AutoInputHelperSourceMixin,
    _AutoInputHelperSubscriptionMixin,
)


class AutoInputHelper(
    _AutoInputHelperSnapshotMixin,
    _AutoInputHelperSubscriptionMixin,
    _AutoInputHelperSourceMixin,
):
    SNAPSHOT_SCHEMA_VERSION = AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION

    @staticmethod
    def _dbus_module() -> Any:
        raise RuntimeError("Direct DBus access is disabled; use the DBus gateway adapter")

    def __init__(
        self,
        config_path: str,
        snapshot_path: str | None = None,
        parent_pid: object = None,
        helper_generation: object = None,
        runtime_instance_id: object = None,
    ) -> None:
        parser = self._load_helper_parser(config_path)
        self._init_helper_base_config(
            config_path,
            parser,
            snapshot_path,
            parent_pid,
            helper_generation,
            runtime_instance_id,
        )
        self._init_helper_polling()
        self._init_helper_pv_config()
        self._init_helper_battery_config()
        self._init_helper_grid_config()
        self._init_helper_runtime_config()
        self._init_helper_runtime_state()

    @staticmethod
    def _load_helper_parser(config_path: str) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        loaded = parser.read(config_path)
        if not loaded or "DEFAULT" not in parser:
            raise ValueError(f"Unable to read config file: {config_path}")
        return parser

    def _init_helper_base_config(
        self,
        config_path: str,
        parser: configparser.ConfigParser,
        snapshot_path: str | None,
        parent_pid: object,
        helper_generation: object,
        runtime_instance_id: object,
    ) -> None:
        self.config_path = config_path
        self.config = parser["DEFAULT"]
        self.parent_pid = self._parsed_parent_pid(parent_pid)
        self.helper_generation = self._parsed_helper_generation(helper_generation)
        self.runtime_instance_id = self._parsed_runtime_instance_id(runtime_instance_id)
        self.snapshot_path = snapshot_path or self.config.get(
            "AutoInputSnapshotPath",
            "/run/dbus-venus-evcharger-auto.json",
        ).strip()
        self.dbus_introspection_snapshot_path = self.config.get("DbusIntrospectionSnapshotPath", "").strip()
        self.dbus_introspection_request_path = self.config.get("DbusIntrospectionRequestPath", "").strip()
        self.dbus_introspection_max_age_seconds = max(
            0.0,
            float(self.config.get("DbusIntrospectionMaxAgeSeconds", 900.0) or 900.0),
        )
        self.dbus_gateway_run_dir = self.config.get("DbusGatewayRunDir", "/run/venus-evcharger").strip()
        self.dbus_gateway_cache_path = self.config.get(
            "DbusGatewayCachePath",
            os.path.join(self.dbus_gateway_run_dir, "dbus-cache.json"),
        ).strip()
        self.dbus_gateway_max_age_seconds = max(
            0.0,
            float(self.config.get("DbusGatewayMaxAgeSeconds", 10.0) or 10.0),
        )
        self.dbus_method_timeout_seconds = float(self.config.get("DbusMethodTimeoutSeconds", 1.0))

    @staticmethod
    def _parsed_parent_pid(parent_pid: object) -> int | None:
        if isinstance(parent_pid, (str, int)):
            return int(parent_pid)
        return None

    @staticmethod
    def _parsed_helper_generation(helper_generation: object) -> int:
        if isinstance(helper_generation, (str, int)):
            return max(0, int(helper_generation))
        return 0

    @staticmethod
    def _parsed_runtime_instance_id(runtime_instance_id: object) -> str:
        if isinstance(runtime_instance_id, str) and runtime_instance_id.strip():
            return runtime_instance_id.strip()
        return uuid.uuid4().hex

    def _init_helper_polling(self) -> None:
        auto_input_poll_interval_ms = self._auto_input_poll_interval_ms()
        self.auto_pv_poll_interval_seconds = self._poll_interval_seconds(
            "AutoPvPollIntervalMs",
            auto_input_poll_interval_ms,
        )
        self.auto_grid_poll_interval_seconds = self._poll_interval_seconds(
            "AutoGridPollIntervalMs",
            auto_input_poll_interval_ms,
        )
        self.auto_battery_poll_interval_seconds = self._poll_interval_seconds(
            "AutoBatteryPollIntervalMs",
            auto_input_poll_interval_ms,
        )
        self.poll_interval_seconds = min(
            max(0.2, auto_input_poll_interval_ms / 1000.0),
            self.auto_pv_poll_interval_seconds,
            self.auto_grid_poll_interval_seconds,
            self.auto_battery_poll_interval_seconds,
        )

    def _auto_input_poll_interval_ms(self) -> float:
        return float(
            self.config.get(
                "AutoInputPollIntervalMs",
                self.config.get("PollIntervalMs", 1000),
            )
        )

    def _poll_interval_seconds(self, key: str, fallback_ms: float) -> float:
        return max(0.2, float(self.config.get(key, fallback_ms)) / 1000.0)

    def _init_helper_pv_config(self) -> None:
        self.auto_pv_service = self.config.get("AutoPvService", "").strip()
        self.auto_pv_service_prefix = self.config.get("AutoPvServicePrefix", "com.victronenergy.pvinverter").strip()
        self.auto_pv_path = self.config.get("AutoPvPath", "/Ac/Power").strip()
        self.auto_pv_max_services = max(1, int(self.config.get("AutoPvMaxServices", 10)))
        self.auto_pv_scan_interval_seconds = max(0.0, float(self.config.get("AutoPvScanIntervalSeconds", 60)))
        self.auto_use_dc_pv = _as_bool(self.config.get("AutoUseDcPv", "1"), True)
        self.auto_dc_pv_service = self.config.get("AutoDcPvService", "com.victronenergy.system").strip()
        self.auto_dc_pv_path = self.config.get("AutoDcPvPath", "/Dc/Pv/Power").strip()

    def _init_helper_battery_config(self) -> None:
        self._init_helper_battery_identity_config()
        self._init_helper_battery_capacity_config()
        self._init_helper_battery_power_config()
        self._init_helper_battery_discovery_config()
        self.auto_energy_sources, self.auto_use_combined_battery_soc = load_energy_source_settings(self.config)
        self.auto_energy_source_ids = tuple(source.source_id for source in self.auto_energy_sources)

    def _init_helper_battery_identity_config(self) -> None:
        self.auto_battery_service = self.config.get(
            "AutoBatteryService",
            "com.victronenergy.battery.socketcan_can1",
        ).strip()
        self.auto_battery_soc_path = self.config.get("AutoBatterySocPath", "/Soc").strip()

    def _init_helper_battery_capacity_config(self) -> None:
        self.auto_battery_capacity_wh = float(self.config.get("AutoBatteryCapacityWh", 0) or 0)
        self.auto_battery_chemistry = self.config.get("AutoBatteryChemistry", "lfp").strip().lower()
        self.auto_battery_capacity_auto_estimate = _as_bool(self.config.get("AutoBatteryCapacityAutoEstimate", "1"), True)
        self.auto_battery_capacity_wh_path = self.config.get("AutoBatteryCapacityWhPath", "").strip()
        self.auto_battery_capacity_ah_path = self.config.get("AutoBatteryCapacityAhPath", "/InstalledCapacity").strip()
        self.auto_battery_voltage_path = self.config.get("AutoBatteryVoltagePath", "/Dc/0/Voltage").strip()
        self._init_helper_battery_capacity_thresholds()
        self._init_helper_battery_estimated_capacity_config()

    def _init_helper_battery_capacity_thresholds(self) -> None:
        self.auto_battery_capacity_estimate_min_soc = max(
            0.0,
            float(self.config.get("AutoBatteryCapacityEstimateMinSoc", 95) or 95),
        )
        self.auto_battery_capacity_startup_recheck_seconds = max(
            0.0,
            float(self.config.get("AutoBatteryCapacityStartupRecheckSeconds", 300) or 300),
        )

    def _init_helper_battery_estimated_capacity_config(self) -> None:
        self.auto_battery_capacity_estimated_wh = float(self.config.get("AutoBatteryCapacityEstimatedWh", 0) or 0)
        self.auto_battery_capacity_estimated_ah = float(self.config.get("AutoBatteryCapacityEstimatedAh", 0) or 0)
        self.auto_battery_capacity_estimated_nominal_voltage = float(
            self.config.get("AutoBatteryCapacityEstimatedNominalVoltage", 0) or 0
        )
        self.auto_battery_capacity_estimated_cell_count = int(
            float(self.config.get("AutoBatteryCapacityEstimatedCellCount", 0) or 0)
        )

    def _init_helper_battery_power_config(self) -> None:
        self.auto_battery_power_path = self.config.get("AutoBatteryPowerPath", "").strip()
        self.auto_battery_ac_power_path = self.config.get("AutoBatteryAcPowerPath", "").strip()
        self.auto_battery_pv_power_path = self.config.get("AutoBatteryPvPowerPath", "").strip()
        self.auto_battery_grid_interaction_path = self.config.get("AutoBatteryGridInteractionPath", "").strip()
        self.auto_battery_operating_mode_path = self.config.get("AutoBatteryOperatingModePath", "").strip()

    def _init_helper_battery_discovery_config(self) -> None:
        self.auto_battery_service_prefix = self.config.get(
            "AutoBatteryServicePrefix",
            "com.victronenergy.battery",
        ).strip()
        self.auto_battery_scan_interval_seconds = max(
            0.0,
            float(self.config.get("AutoBatteryScanIntervalSeconds", 60)),
        )

    def _init_helper_grid_config(self) -> None:
        self.auto_grid_service = self.config.get("AutoGridService", "com.victronenergy.system").strip()
        self.auto_grid_l1_path = self.config.get("AutoGridL1Path", "/Ac/Grid/L1/Power").strip()
        self.auto_grid_l2_path = self.config.get("AutoGridL2Path", "/Ac/Grid/L2/Power").strip()
        self.auto_grid_l3_path = self.config.get("AutoGridL3Path", "/Ac/Grid/L3/Power").strip()
        self.auto_grid_require_all_phases = _as_bool(
            self.config.get("AutoGridRequireAllPhases", "1"),
            True,
        )

    def _init_helper_runtime_config(self) -> None:
        self.auto_dbus_backoff_base_seconds = max(
            0.0,
            float(self.config.get("AutoDbusBackoffBaseSeconds", 5)),
        )
        self.auto_dbus_backoff_max_seconds = max(
            0.0,
            float(self.config.get("AutoDbusBackoffMaxSeconds", 60)),
        )
        self.validation_poll_seconds = max(
            5.0,
            float(self.config.get("AutoInputValidationPollSeconds", 30)),
        )
        self.subscription_refresh_seconds = self._derive_subscription_refresh_seconds()

    def _init_helper_runtime_state(self) -> None:
        self._system_bus = None
        self._dbus_generation = 0
        self._system_bus_generation = 0
        self._name_owner_match = None
        self._dbus_subscription_backoff_until = 0.0
        self._dbus_list_backoff_until = 0.0
        self._dbus_list_failures = 0
        self._resolved_auto_pv_services = []
        self._auto_pv_last_scan = 0.0
        self._resolved_auto_battery_service = None
        self._auto_battery_last_scan = 0.0
        self._resolved_auto_energy_services: dict[str, str] = {}
        self._auto_energy_last_scan: dict[str, float] = {}
        self._auto_battery_capacity_estimates: dict[str, dict[str, object]] = {}
        self._auto_battery_capacity_startup_recheck_at = (
            time.time() + self.auto_battery_capacity_startup_recheck_seconds
            if self.auto_battery_capacity_startup_recheck_seconds > 0.0
            else 0.0
        )
        self._auto_battery_capacity_startup_rechecked: dict[str, bool] = {}
        self._energy_learning_profiles: dict[str, Any] = {}
        self._source_retry_after: dict[str, float] = {}
        self._warning_state: dict[str, float] = {}
        self._last_payload: str | None = None
        self._last_snapshot_state: dict[str, object] = self._empty_snapshot()
        self._snapshot_lock = threading.RLock()
        self._heartbeat_thread_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._parent_watchdog_thread: threading.Thread | None = None
        self._next_source_poll_at = {
            "pv": 0.0,
            "battery": 0.0,
            "grid": 0.0,
        }
        self._signal_matches: dict[tuple[str, str, str], Any] = {}
        self._monitored_specs: dict[tuple[str, str, str], dict[str, str]] = {}
        self._refresh_scheduled = False
        self._main_loop: Any = None
        self._stop_requested = False

    def _handle_signal(self, signum: int, _frame: object) -> None:
        """Stop the helper cleanly when asked."""
        logging.info("Auto input helper received signal %s", signum)
        self._stop_requested = True
        if self._main_loop is not None:
            GLib.idle_add(self._main_loop.quit)

    def _derive_subscription_refresh_seconds(self) -> float:
        """Return a slow service refresh interval for DBus subscription bookkeeping."""
        candidates = [60.0]
        for value in (
            float(self.config.get("AutoPvScanIntervalSeconds", 60)),
            float(self.config.get("AutoBatteryScanIntervalSeconds", 60)),
        ):
            if value > 0:
                candidates.append(value)
        return max(5.0, min(candidates))

    def _parent_alive(self) -> bool:
        """Return False when the parent process is gone."""
        if self.parent_pid is None:
            return True
        try:
            return bool(os.getppid() == self.parent_pid)
        except Exception:  # pylint: disable=broad-except
            return False

    def _warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
    ) -> None:
        """Log a warning only once per interval for a given issue."""
        now = time.time()
        last_logged = self._warning_state.get(key)
        if last_logged is None or (now - last_logged) > interval_seconds:
            logging.warning(message, *args)
            self._warning_state[key] = now

    @staticmethod
    def _empty_snapshot(captured_at: float | None = None) -> dict[str, object]:
        """Return an empty helper snapshot payload."""
        return {
            "snapshot_version": AutoInputHelper.SNAPSHOT_SCHEMA_VERSION,
            "captured_at": captured_at,
            "heartbeat_at": captured_at,
            "writer_pid": os.getpid(),
            "helper_state": "starting",
            "helper_status": "starting",
            "pv_status": "missing",
            "pv_captured_at": None,
            "pv_power": None,
            "battery_status": "missing",
            "battery_captured_at": None,
            "battery_soc": None,
            "battery_combined_soc": None,
            "battery_combined_usable_capacity_wh": None,
            "battery_combined_charge_power_w": None,
            "battery_combined_discharge_power_w": None,
            "battery_combined_net_power_w": None,
            "battery_combined_ac_power_w": None,
            "battery_source_count": 0,
            "battery_online_source_count": 0,
            "battery_valid_soc_source_count": 0,
            "battery_sources": [],
            "battery_learning_profiles": {},
            "grid_status": "missing",
            "grid_captured_at": None,
            "grid_power": None,
        }

    def _write_snapshot(self, payload: dict[str, object]) -> None:
        """Persist the helper snapshot atomically in RAM."""
        normalized_payload = dict(payload)
        normalized_payload.setdefault("snapshot_version", self.SNAPSHOT_SCHEMA_VERSION)
        normalized_payload["writer_pid"] = os.getpid()
        normalized_payload["helper_generation"] = int(getattr(self, "helper_generation", 0) or 0)
        normalized_payload["runtime_instance_id"] = str(getattr(self, "runtime_instance_id", "") or "")
        serialized = compact_json(normalized_payload)
        if serialized == self._last_payload:
            return
        write_text_atomically(self.snapshot_path, serialized)
        self._last_payload = serialized

    def _write_lifecycle_snapshot(self, state: str, now: float | None = None) -> None:
        """Write a fresh helper liveness snapshot independent of DBus reads."""
        current = time.time() if now is None else float(now)
        self._ensure_liveness_thread_state()
        with self._snapshot_guard():
            snapshot = dict(self._last_snapshot_state)
            snapshot["captured_at"] = current
            snapshot["heartbeat_at"] = current
            snapshot["helper_state"] = state
            snapshot["helper_status"] = state
            snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
            self._stamp_snapshot_metadata(snapshot)
            self._last_snapshot_state = snapshot
            self._write_snapshot(snapshot)

    @staticmethod
    def _require_dbus_glib_mainloop() -> None:
        """Compatibility hook; DBus mainloop setup now belongs to the gateway."""
        return None

    @staticmethod
    def _signal_values() -> tuple[int, ...]:
        """Return supported process signals for clean helper shutdown."""
        return tuple(
            signum
            for signum in (
                getattr(signal, "SIGTERM", None),
                getattr(signal, "SIGINT", None),
                getattr(signal, "SIGHUP", None),
            )
            if signum is not None
        )

    def _install_signal_handlers(self) -> None:
        """Install signal handlers for graceful helper shutdown."""
        for signum in self._signal_values():
            try:
                signal.signal(signum, self._handle_signal)
            except Exception:  # pylint: disable=broad-except
                pass

    def _log_helper_start(self) -> None:
        """Log one startup banner for the helper process."""
        logging.info(
            "Start auto input helper pid=%s parent=%s snapshot=%s",
            os.getpid(),
            self.parent_pid,
            self.snapshot_path,
        )

    def _install_main_loop_timers(self) -> None:
        """Install the periodic timers used by the helper main loop."""
        GLib.timeout_add(max(5000, int(self.validation_poll_seconds * 1000)), self._validation_poll)
        GLib.timeout_add(max(1000, int(self.subscription_refresh_seconds * 1000)), self._refresh_subscriptions_timer)
        GLib.timeout_add(1000, self._parent_watchdog)

    def _start_liveness_threads(self) -> None:
        """Start RAM-only liveness workers before DBus work can block."""
        self._ensure_liveness_thread_state()
        self._heartbeat_thread_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="auto-input-heartbeat",
            daemon=True,
        )
        self._parent_watchdog_thread = threading.Thread(
            target=self._parent_watchdog_loop,
            name="auto-input-parent-watchdog",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._parent_watchdog_thread.start()

    def _stop_liveness_threads(self) -> None:
        self._ensure_liveness_thread_state()
        self._heartbeat_thread_stop.set()
        for thread in (self._heartbeat_thread, self._parent_watchdog_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)

    def _ensure_liveness_thread_state(self) -> None:
        """Ensure liveness threads can start in partial construction paths."""
        defaults = (
            ("_stop_requested", lambda: False),
            ("_snapshot_lock", threading.RLock),
            ("_heartbeat_thread_stop", threading.Event),
            ("_heartbeat_thread", lambda: None),
            ("_parent_watchdog_thread", lambda: None),
        )
        for name, factory in defaults:
            if not hasattr(self, name):
                setattr(self, name, factory())

    def _heartbeat_loop(self) -> None:
        interval = max(0.5, min(2.0, float(getattr(self, "poll_interval_seconds", 1.0) or 1.0)))
        while not self._heartbeat_thread_stop.wait(interval):
            if self._stop_requested:
                return
            try:
                self._heartbeat_snapshot()
            except Exception as error:  # pylint: disable=broad-except
                logging.debug("Auto input helper heartbeat write failed: %s", error)

    def _parent_watchdog_loop(self) -> None:
        while not self._heartbeat_thread_stop.wait(1.0):
            if self._stop_requested:
                return
            if self._parent_alive():
                continue
            self._stop_requested = True
            if self._main_loop is not None:
                GLib.idle_add(self._main_loop.quit)
            return

    def _schedule_initial_dbus_refresh(self) -> None:
        """Defer DBus subscription work until heartbeat and parent timers exist."""
        self._refresh_scheduled = True

        def _run() -> bool:
            self._refresh_scheduled = False
            if self._stop_requested:
                return False
            self._write_lifecycle_snapshot("initializing")
            self._refresh_subscriptions()
            return False

        GLib.idle_add(_run)

    def _build_main_loop(self) -> Any:
        """Create and remember the GLib main loop used by the helper."""
        self._main_loop = GLib.MainLoop()
        return self._main_loop

    def run(self) -> None:
        """Main helper loop using gateway cache refreshes plus a small RAM heartbeat."""
        self._require_dbus_glib_mainloop()
        self._install_signal_handlers()
        self._log_helper_start()
        self._build_main_loop()
        self._write_lifecycle_snapshot("starting")
        self._start_liveness_threads()
        self._install_main_loop_timers()
        self._schedule_initial_dbus_refresh()
        assert self._main_loop is not None
        try:
            self._main_loop.run()
        finally:
            self._stop_requested = True
            self._stop_liveness_threads()
            self._reset_system_bus()
            logging.info("Auto input helper stopping pid=%s", os.getpid())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _main_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=logging.INFO,
    )
    if args.runtime_instance_id is None:
        helper = AutoInputHelper(args.config_path, args.snapshot_path, args.parent_pid, args.helper_generation)
    else:
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


if __name__ == "__main__":
    raise SystemExit(main())
