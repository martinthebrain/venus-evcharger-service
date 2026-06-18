# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration initialization helpers for the auto-input helper process."""

from __future__ import annotations

import os
import configparser
from collections.abc import Callable

from venus_evcharger.core.shared import parse_config_bool as _as_bool
from venus_evcharger.energy import load_energy_source_settings


class _AutoInputHelperConfigMixin:
    _derive_subscription_refresh_seconds: Callable[[], float]
    _parsed_helper_generation: Callable[[object], int]
    _parsed_parent_pid: Callable[[object], int | None]
    _parsed_runtime_instance_id: Callable[[object], str]

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
