# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed configuration for the auto-input helper process."""

from __future__ import annotations

import configparser
import os
import uuid
from dataclasses import dataclass

from venus_evcharger.core.shared import config_get_float, parse_config_bool
from venus_evcharger.energy import DEFAULT_BATTERY_CHEMISTRY, EnergySourceDefinition, load_energy_source_settings
from venus_evcharger.energy.grid_fusion_contracts import GridFusionConfig


@dataclass(frozen=True, slots=True)
class AutoInputHelperSettings:
    """Complete immutable configuration consumed by helper components."""

    config_path: str
    snapshot_path: str
    parent_pid: int | None
    helper_generation: int
    runtime_instance_id: str
    dbus_introspection_snapshot_path: str
    dbus_introspection_request_path: str
    dbus_introspection_max_age_seconds: float
    dbus_gateway_run_dir: str
    dbus_gateway_cache_path: str
    dbus_gateway_max_age_seconds: float
    dbus_gateway_error_retry_seconds: float
    dbus_method_timeout_seconds: float
    auto_pv_poll_interval_seconds: float
    auto_grid_poll_interval_seconds: float
    auto_battery_poll_interval_seconds: float
    poll_interval_seconds: float
    auto_pv_service: str
    auto_pv_service_prefix: str
    auto_pv_path: str
    auto_pv_max_services: int
    auto_pv_scan_interval_seconds: float
    auto_use_dc_pv: bool
    auto_dc_pv_service: str
    auto_dc_pv_path: str
    auto_battery_service: str
    auto_battery_soc_path: str
    auto_battery_capacity_wh: float
    auto_battery_chemistry: str
    auto_battery_capacity_auto_estimate: bool
    auto_battery_capacity_wh_path: str
    auto_battery_capacity_ah_path: str
    auto_battery_voltage_path: str
    auto_battery_capacity_estimate_min_soc: float
    auto_battery_capacity_startup_recheck_seconds: float
    auto_battery_capacity_estimated_wh: float
    auto_battery_capacity_estimated_ah: float
    auto_battery_capacity_estimated_nominal_voltage: float
    auto_battery_capacity_estimated_cell_count: int
    auto_battery_power_path: str
    auto_battery_ac_power_path: str
    auto_battery_pv_power_path: str
    auto_battery_grid_interaction_path: str
    auto_battery_operating_mode_path: str
    auto_battery_service_prefix: str
    auto_battery_scan_interval_seconds: float
    auto_energy_sources: tuple[EnergySourceDefinition, ...]
    auto_use_combined_battery_soc: bool
    auto_grid_service: str
    auto_grid_l1_path: str
    auto_grid_l2_path: str
    auto_grid_l3_path: str
    auto_grid_require_all_phases: bool
    grid_fusion_config: GridFusionConfig
    auto_dbus_backoff_base_seconds: float
    auto_dbus_backoff_max_seconds: float
    validation_poll_seconds: float
    subscription_refresh_seconds: float


def load_auto_input_helper_settings(
    config_path: str,
    snapshot_path: str | None,
    parent_pid: object,
    helper_generation: object,
    runtime_instance_id: object,
) -> AutoInputHelperSettings:
    """Load and validate one complete helper configuration."""
    parser = configparser.ConfigParser()
    loaded = parser.read(config_path)
    _require_loaded_config(parser, loaded, config_path)
    config = parser["DEFAULT"]
    poll_fallback_ms = config_get_float(config, "PollIntervalMs", 1000.0)
    auto_poll_ms = config_get_float(config, "AutoInputPollIntervalMs", poll_fallback_ms)
    pv_poll = _poll_interval_seconds(config, "AutoPvPollIntervalMs", auto_poll_ms)
    grid_poll = _poll_interval_seconds(config, "AutoGridPollIntervalMs", auto_poll_ms)
    battery_poll = _poll_interval_seconds(config, "AutoBatteryPollIntervalMs", auto_poll_ms)
    energy_sources, use_combined_soc = load_energy_source_settings(config)
    fusion = _grid_fusion_config(config)
    _validate_grid_fusion_poll_interval(fusion, battery_poll)
    return AutoInputHelperSettings(
        config_path=config_path,
        snapshot_path=snapshot_path or config.get("AutoInputSnapshotPath", "/run/dbus-venus-evcharger-auto.json").strip(),
        parent_pid=_parsed_parent_pid(parent_pid),
        helper_generation=_parsed_helper_generation(helper_generation),
        runtime_instance_id=_parsed_runtime_instance_id(runtime_instance_id),
        dbus_introspection_snapshot_path=config.get("DbusIntrospectionSnapshotPath", "").strip(),
        dbus_introspection_request_path=config.get("DbusIntrospectionRequestPath", "").strip(),
        dbus_introspection_max_age_seconds=max(
            0.0, config_get_float(config, "DbusIntrospectionMaxAgeSeconds", 900.0)
        ),
        dbus_gateway_run_dir=config.get("DbusGatewayRunDir", "/run/venus-evcharger").strip(),
        dbus_gateway_cache_path=_gateway_cache_path(config),
        dbus_gateway_max_age_seconds=max(0.0, config_get_float(config, "DbusGatewayMaxAgeSeconds", 10.0)),
        dbus_gateway_error_retry_seconds=max(
            1.0, min(300.0, config_get_float(config, "DbusGatewayErrorRetrySeconds", 30.0))
        ),
        dbus_method_timeout_seconds=config_get_float(config, "DbusMethodTimeoutSeconds", 1.0),
        auto_pv_poll_interval_seconds=pv_poll,
        auto_grid_poll_interval_seconds=grid_poll,
        auto_battery_poll_interval_seconds=battery_poll,
        poll_interval_seconds=min(max(0.2, auto_poll_ms / 1000.0), pv_poll, grid_poll, battery_poll),
        auto_pv_service=config.get("AutoPvService", "").strip(),
        auto_pv_service_prefix=config.get("AutoPvServicePrefix", "com.victronenergy.pvinverter").strip(),
        auto_pv_path=config.get("AutoPvPath", "/Ac/Power").strip(),
        auto_pv_max_services=max(1, int(config_get_float(config, "AutoPvMaxServices", 10.0))),
        auto_pv_scan_interval_seconds=max(0.0, config_get_float(config, "AutoPvScanIntervalSeconds", 60.0)),
        auto_use_dc_pv=parse_config_bool(config.get("AutoUseDcPv", "1")),
        auto_dc_pv_service=config.get("AutoDcPvService", "com.victronenergy.system").strip(),
        auto_dc_pv_path=config.get("AutoDcPvPath", "/Dc/Pv/Power").strip(),
        auto_battery_service=config.get("AutoBatteryService", "com.victronenergy.battery.socketcan_can1").strip(),
        auto_battery_soc_path=config.get("AutoBatterySocPath", "/Soc").strip(),
        auto_battery_capacity_wh=config_get_float(config, "AutoBatteryCapacityWh", 0.0),
        auto_battery_chemistry=config.get("AutoBatteryChemistry", DEFAULT_BATTERY_CHEMISTRY).strip().lower(),
        auto_battery_capacity_auto_estimate=parse_config_bool(config.get("AutoBatteryCapacityAutoEstimate", "1")),
        auto_battery_capacity_wh_path=config.get("AutoBatteryCapacityWhPath", "").strip(),
        auto_battery_capacity_ah_path=config.get("AutoBatteryCapacityAhPath", "/InstalledCapacity").strip(),
        auto_battery_voltage_path=config.get("AutoBatteryVoltagePath", "/Dc/0/Voltage").strip(),
        auto_battery_capacity_estimate_min_soc=max(
            0.0, config_get_float(config, "AutoBatteryCapacityEstimateMinSoc", 95.0)
        ),
        auto_battery_capacity_startup_recheck_seconds=max(
            0.0, config_get_float(config, "AutoBatteryCapacityStartupRecheckSeconds", 300.0)
        ),
        auto_battery_capacity_estimated_wh=config_get_float(config, "AutoBatteryCapacityEstimatedWh", 0.0),
        auto_battery_capacity_estimated_ah=config_get_float(config, "AutoBatteryCapacityEstimatedAh", 0.0),
        auto_battery_capacity_estimated_nominal_voltage=config_get_float(
            config, "AutoBatteryCapacityEstimatedNominalVoltage", 0.0
        ),
        auto_battery_capacity_estimated_cell_count=int(
            config_get_float(config, "AutoBatteryCapacityEstimatedCellCount", 0.0)
        ),
        auto_battery_power_path=config.get("AutoBatteryPowerPath", "").strip(),
        auto_battery_ac_power_path=config.get("AutoBatteryAcPowerPath", "").strip(),
        auto_battery_pv_power_path=config.get("AutoBatteryPvPowerPath", "").strip(),
        auto_battery_grid_interaction_path=config.get("AutoBatteryGridInteractionPath", "").strip(),
        auto_battery_operating_mode_path=config.get("AutoBatteryOperatingModePath", "").strip(),
        auto_battery_service_prefix=config.get("AutoBatteryServicePrefix", "com.victronenergy.battery").strip(),
        auto_battery_scan_interval_seconds=max(
            0.0, config_get_float(config, "AutoBatteryScanIntervalSeconds", 60.0)
        ),
        auto_energy_sources=tuple(energy_sources),
        auto_use_combined_battery_soc=use_combined_soc,
        auto_grid_service=config.get("AutoGridService", "com.victronenergy.system").strip(),
        auto_grid_l1_path=config.get("AutoGridL1Path", "/Ac/Grid/L1/Power").strip(),
        auto_grid_l2_path=config.get("AutoGridL2Path", "/Ac/Grid/L2/Power").strip(),
        auto_grid_l3_path=config.get("AutoGridL3Path", "/Ac/Grid/L3/Power").strip(),
        auto_grid_require_all_phases=parse_config_bool(config.get("AutoGridRequireAllPhases", "1")),
        grid_fusion_config=fusion,
        auto_dbus_backoff_base_seconds=max(0.0, config_get_float(config, "AutoDbusBackoffBaseSeconds", 5.0)),
        auto_dbus_backoff_max_seconds=max(0.0, config_get_float(config, "AutoDbusBackoffMaxSeconds", 60.0)),
        validation_poll_seconds=max(5.0, config_get_float(config, "AutoInputValidationPollSeconds", 30.0)),
        subscription_refresh_seconds=_subscription_refresh_seconds(config),
    )


def _gateway_cache_path(config: configparser.SectionProxy) -> str:
    run_dir = config.get("DbusGatewayRunDir", "/run/venus-evcharger").strip()
    return config.get("DbusGatewayCachePath", os.path.join(run_dir, "dbus-cache.json")).strip()


def _require_loaded_config(parser: configparser.ConfigParser, loaded: list[str], config_path: str) -> None:
    if not loaded or "DEFAULT" not in parser:
        raise ValueError(f"Unable to read config file: {config_path}")


def _validate_grid_fusion_poll_interval(fusion: GridFusionConfig, battery_poll: float) -> None:
    if fusion.enabled and fusion.primary_max_age_seconds < battery_poll:
        raise ValueError("AutoGridFusionPrimaryMaxAgeSeconds must cover AutoBatteryPollIntervalMs")


def _poll_interval_seconds(config: configparser.SectionProxy, key: str, fallback_ms: float) -> float:
    return max(0.2, config_get_float(config, key, fallback_ms) / 1000.0)


def _subscription_refresh_seconds(config: configparser.SectionProxy) -> float:
    candidates = [60.0]
    for value in (
        config_get_float(config, "AutoPvScanIntervalSeconds", 60.0),
        config_get_float(config, "AutoBatteryScanIntervalSeconds", 60.0),
    ):
        if value > 0.0:
            candidates.append(value)
    return max(5.0, min(candidates))


def _grid_fusion_config(config: configparser.SectionProxy) -> GridFusionConfig:
    return GridFusionConfig(
        enabled=parse_config_bool(config["AutoGridFusionEnabled"]) if "AutoGridFusionEnabled" in config else False,
        primary_source_id=config.get("AutoGridFusionPrimarySource", "").strip(),
        backup_source_id=config.get("AutoGridFusionBackupSource", "victron").strip(),
        primary_max_age_seconds=config_get_float(config, "AutoGridFusionPrimaryMaxAgeSeconds", 15.0),
        backup_max_age_seconds=config_get_float(config, "AutoGridFusionBackupMaxAgeSeconds", 6.0),
        minimum_confidence=config_get_float(config, "AutoGridFusionMinimumConfidence", 0.5),
        failover_samples=int(config_get_float(config, "AutoGridFusionFailoverSamples", 3.0)),
        recovery_samples=int(config_get_float(config, "AutoGridFusionRecoverySamples", 15.0)),
        failover_hold_seconds=config_get_float(config, "AutoGridFusionFailoverHoldSeconds", 6.0),
        mismatch_absolute_watts=config_get_float(config, "AutoGridFusionMismatchAbsoluteWatts", 300.0),
        mismatch_relative=config_get_float(config, "AutoGridFusionMismatchRelative", 0.15),
        mismatch_samples=int(config_get_float(config, "AutoGridFusionMismatchSamples", 3.0)),
        future_tolerance_seconds=config_get_float(config, "AutoGridFusionFutureToleranceSeconds", 1.0),
    )


def _parsed_parent_pid(value: object) -> int | None:
    return int(value) if isinstance(value, (str, int)) else None


def _parsed_helper_generation(value: object) -> int:
    return max(0, int(value)) if isinstance(value, (str, int)) else 0


def _parsed_runtime_instance_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return uuid.uuid4().hex
