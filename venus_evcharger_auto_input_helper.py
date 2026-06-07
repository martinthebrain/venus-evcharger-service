#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Venus EV charger service in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import configparser
import logging
import os
import signal
import sys
import time
from typing import Any

import dbus
from gi.repository import GLib
from venus_evcharger.core.shared import (
    AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
    compact_json,
    write_text_atomically,
)
from venus_evcharger.energy import load_energy_source_settings

try:
    import dbus.mainloop.glib as dbus_glib_mainloop
except Exception:  # pylint: disable=broad-except
    dbus_glib_mainloop = None


_TEST_PATCH_EXPORTS = (dbus, GLib, time)


def _as_bool(value: object, default: bool = False) -> bool:
    """Parse a config-style truthy value."""
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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

    def __init__(
        self,
        config_path: str,
        snapshot_path: str | None = None,
        parent_pid: object = None,
        helper_generation: object = None,
    ) -> None:
        parser = self._load_helper_parser(config_path)
        self._init_helper_base_config(config_path, parser, snapshot_path, parent_pid, helper_generation)
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
    ) -> None:
        self.config_path = config_path
        self.config = parser["DEFAULT"]
        self.parent_pid = self._parsed_parent_pid(parent_pid)
        self.helper_generation = self._parsed_helper_generation(helper_generation)
        self.snapshot_path = snapshot_path or self.config.get(
            "AutoInputSnapshotPath",
            "/run/dbus-venus-evcharger-auto.json",
        ).strip()
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
        self.auto_battery_service = self.config.get(
            "AutoBatteryService",
            "com.victronenergy.battery.socketcan_can1",
        ).strip()
        self.auto_battery_soc_path = self.config.get("AutoBatterySocPath", "/Soc").strip()
        self.auto_battery_capacity_wh = float(self.config.get("AutoBatteryCapacityWh", 0) or 0)
        self.auto_battery_power_path = self.config.get("AutoBatteryPowerPath", "").strip()
        self.auto_battery_ac_power_path = self.config.get("AutoBatteryAcPowerPath", "").strip()
        self.auto_battery_pv_power_path = self.config.get("AutoBatteryPvPowerPath", "").strip()
        self.auto_battery_grid_interaction_path = self.config.get("AutoBatteryGridInteractionPath", "").strip()
        self.auto_battery_operating_mode_path = self.config.get("AutoBatteryOperatingModePath", "").strip()
        self.auto_battery_service_prefix = self.config.get(
            "AutoBatteryServicePrefix",
            "com.victronenergy.battery",
        ).strip()
        self.auto_battery_scan_interval_seconds = max(
            0.0,
            float(self.config.get("AutoBatteryScanIntervalSeconds", 60)),
        )
        self.auto_energy_sources, self.auto_use_combined_battery_soc = load_energy_source_settings(self.config)
        self.auto_energy_source_ids = tuple(source.source_id for source in self.auto_energy_sources)

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
        self._energy_learning_profiles: dict[str, Any] = {}
        self._source_retry_after: dict[str, float] = {}
        self._warning_state: dict[str, float] = {}
        self._last_payload: str | None = None
        self._last_snapshot_state: dict[str, object] = self._empty_snapshot()
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
            "pv_captured_at": None,
            "pv_power": None,
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
            "grid_captured_at": None,
            "grid_power": None,
        }

    def _write_snapshot(self, payload: dict[str, object]) -> None:
        """Persist the helper snapshot atomically in RAM."""
        normalized_payload = dict(payload)
        normalized_payload.setdefault("snapshot_version", self.SNAPSHOT_SCHEMA_VERSION)
        normalized_payload["writer_pid"] = os.getpid()
        normalized_payload["helper_generation"] = int(getattr(self, "helper_generation", 0) or 0)
        serialized = compact_json(normalized_payload)
        if serialized == self._last_payload:
            return
        write_text_atomically(self.snapshot_path, serialized)
        self._last_payload = serialized

    @staticmethod
    def _require_dbus_glib_mainloop() -> Any:
        """Return the DBus GLib mainloop module or fail with a clear helper error."""
        if dbus_glib_mainloop is None:
            raise RuntimeError("dbus.mainloop.glib is required for the auto input helper")
        return dbus_glib_mainloop

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
        GLib.timeout_add(max(500, int(self.poll_interval_seconds * 1000)), self._heartbeat_snapshot)
        GLib.timeout_add(max(5000, int(self.validation_poll_seconds * 1000)), self._validation_poll)
        GLib.timeout_add(max(1000, int(self.subscription_refresh_seconds * 1000)), self._refresh_subscriptions_timer)
        GLib.timeout_add(1000, self._parent_watchdog)

    def _build_main_loop(self) -> Any:
        """Create and remember the GLib main loop used by the helper."""
        self._main_loop = GLib.MainLoop()
        return self._main_loop

    def run(self) -> None:
        """Main helper loop using DBus subscriptions plus a small RAM heartbeat."""
        self._require_dbus_glib_mainloop().DBusGMainLoop(set_as_default=True)
        self._install_signal_handlers()
        self._log_helper_start()
        self._build_main_loop()
        self._register_name_owner_subscription()
        self._refresh_subscriptions()
        self._install_main_loop_timers()
        assert self._main_loop is not None
        try:
            self._main_loop.run()
        finally:
            self._reset_system_bus()
            logging.info("Auto input helper stopping pid=%s", os.getpid())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "deploy",
        "venus",
        "config.venus_evcharger.ini",
    )
    snapshot_path = argv[1] if len(argv) > 1 else None
    parent_pid = argv[2] if len(argv) > 2 else None
    helper_generation = argv[3] if len(argv) > 3 else None
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=logging.INFO,
    )
    helper = AutoInputHelper(config_path, snapshot_path, parent_pid, helper_generation)
    helper.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
